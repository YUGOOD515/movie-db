"""Durable, idempotent association jobs, grouped and scheduled by root title.

Only paths, mappings and probe results are persisted; signed/raw URLs and tokens
are obtained just in time. Each <=100-file chunk retains its atomic transaction.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

from models import import_store as job_store
from models.database import connect_database
from queries.association import get_file_targets
from services import import_runtime as runtime

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_runners = {}
_owner = uuid.uuid4().hex


_connect = job_store._connect


def _signature(item):
    return [item.get("size"), item.get("modified"), item.get("added")]


def submit(database, image_root, selections):
    from services.media_discovery import canonical_path, scan_status

    if not isinstance(selections, list) or not 1 <= len(selections) <= 1000:
        raise ValueError("请选择 1–1000 项待关联内容。")
    scan = scan_status(database, prune=False)
    discovered = {item["path"]: item for record in scan["records"] for item in record["files"]}
    groups, seen = {}, set()
    for selection in selections:
        if not isinstance(selection, dict) or not re.fullmatch(r"tt[0-9]+", str(selection.get("title_id", ""))):
            raise ValueError("作品 IMDb ID 无效。")
        items = selection.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("请选择要关联的视频。")
        group = groups.setdefault(selection["title_id"], {"items": [], "sources": [], "versions": {}})
        source = selection.get("source_name")
        if isinstance(source, str) and source.strip():
            group["sources"].append(source.strip()[:300])
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("视频记录无效。")
            path = canonical_path(item.get("path"))
            if path not in discovered:
                raise ValueError("文件不在本次发现结果中，请刷新待关联列表。")
            if path in seen:
                raise ValueError("同一文件不能重复提交。")
            seen.add(path)
            if len(seen) > 10000:
                raise ValueError("单次最多提交 10000 个视频。")
            group["items"].append({"path": path, "season_number": item.get("season_number"), "episode_number": item.get("episode_number")})
            group["versions"][path] = _signature(discovered[path])

    ids = job_store.enqueue(database, image_root, groups, scan)
    start(database, wake=True)
    return status(database, ids)


status = job_store.status


def _claim(database):
    return job_store._claim(database, _owner)


_progress = job_store._progress


_checkpoint = job_store._checkpoint


def _guard(database, job_id, owner, versions=None):
    if not job_store.owns(database, job_id, owner):
        raise ValueError("关联任务已取消或由其他执行器接管。")
    if versions:
        from services.media_discovery import scan_status
        current = {item["path"]: item for record in scan_status(database, prune=False)["records"] for item in record["files"]}
        if any(path not in current or _signature(current[path]) != version for path, version in versions.items()):
            raise ValueError("源文件已变化，请刷新待关联列表后重新确认。")


def cancel_title(database, title_id):
    with runtime.write_lock:
        job_store.cancel_title(database, title_id)


def _execute(database, row):
    from services.media_association import complete_association, _learn_alias
    from services.media_discovery import scan_status

    job_id, payload = row["job_id"], json.loads(row["payload"])
    try:
        _guard(database, job_id, row["owner"])
        current = {item["path"]: item for record in scan_status(database, prune=False)["records"] for item in record["files"]}
        if any(path not in current or _signature(current[path]) != version for path, version in payload["versions"].items()):
            raise ValueError("源文件已变化，请刷新待关联列表后重新确认。")
        # Reconcile a crash after library commit but before recording job success.
        with closing(connect_database(database)) as db:
            existing = get_file_targets(db, (item["path"] for item in payload["items"]))
        for item in payload["items"]:
            linked = existing.get(item["path"])
            same = linked and (linked["title_id"] == payload["title_id"] or
                               (linked["series_id"] == payload["title_id"] and str(linked["season_number"]) == str(item["season_number"]) and str(linked["episode_number"]) == str(item["episode_number"])))
            if linked and not same:
                raise ValueError("该文件已关联其他影视条目，请核对后重新确认。")
        result = complete_association(library_database=database, image_root=row["image_root"],
            payload={"title_id": payload["title_id"], "items": payload["items"]},
            on_progress=lambda *args: _progress(database, job_id, row["owner"], *args),
            probe_cache=json.loads(row["probes"]),
            on_probe=lambda path, probe: _checkpoint(database, job_id, row["owner"], path, probe),
            defer_enrichment=True, allow_existing=True,
            commit_guard=lambda: _guard(database, job_id, row["owner"], payload["versions"]),
            expected_files=payload["versions"])
        if result["valid"]:
            result["imported"] = len(payload["items"])
            for source in payload["sources"]:
                if result.get("title"):
                    _learn_alias(database, source, result["title"])
        retry = max(result.get("retry_after", 0), result.get("enrichment_retry_after", 0)) if row["attempts"] < 3 else 0
        public_result = {key: result.get(key) for key in ("valid", "imported", "error")}
        public_result["rows"] = [{key: item.get(key) for key in ("index", "path", "error")} for item in result.get("rows", [])]
        state = "queued" if retry else "complete" if result["valid"] else "failed"
        error = "" if result["valid"] else result.get("error") or "部分文件需要核对。"
        if result["valid"] and result.get("enrichment_warning") and not retry:
            public_result["warning"] = "视频已关联，部分图片或评分暂未补齐。"
    except ValueError as failure:
        state, error, public_result, retry = "failed", str(failure), None, 0
    except Exception:
        logger.exception("Association job %s failed", job_id)
        state, error, public_result, retry = "failed", "关联服务暂时不可用，请稍后重试。", None, 0
    job_store.finish(database, row, state, public_result, error, retry)


def _run(database, stop):
    active = {}
    try:
        while not stop.is_set() and Path(database).is_file():
            active = {key: thread for key, thread in active.items() if thread.is_alive()}
            core_limit = runtime.setting("IMPORT_TITLE_CONCURRENCY", 4, 16)
            phases = job_store.running_phases(database, _owner)
            preparing = sum(not phases.get(job_id, {}).get("core") for job_id in active)
            # Optional work has its own bounded window. A slow CDN must not
            # monopolize every title slot while the probe pool sits idle.
            while preparing < core_limit and len(active) < min(32, core_limit * 4):
                row = _claim(database)
                if not row:
                    break
                thread = threading.Thread(target=_execute, args=(database, row), name="title-import", daemon=True)
                active[row["job_id"]] = thread
                thread.start()
                preparing += 1
            next_at = job_store.heartbeat_and_next(database, _owner, active)
            if not active and next_at is None:
                with _lock:
                    # Submission also acquires this lock after its DB commit.
                    pending = job_store.pending(database)
                    if not pending:
                        _runners.pop(str(Path(database).resolve()), None)
                        return
            if not active and next_at is not None and next_at > time.time():
                with _lock:
                    key = str(Path(database).resolve())
                    def wake():
                        with _lock:
                            if stop.is_set():
                                return
                            if _runners.get(key, (None,))[0] is timer:
                                _runners.pop(key, None)
                        start(database)
                    timer = threading.Timer(max(.1, next_at - time.time()), wake)
                    timer.daemon = True
                    _runners[key] = (timer, stop)
                    timer.start()
                return
            stop.wait(.5 if active or next_at is None else max(.1, min(5, next_at - time.time())))
    except Exception:
        logger.exception("Import dispatcher stopped")
    finally:
        for thread in active.values():
            thread.join()


def start(database, *, wake=False):
    key = str(Path(database).resolve())
    with _lock:
        if key in _runners and _runners[key][0].is_alive():
            previous, stop = _runners[key]
            if not wake or not isinstance(previous, threading.Timer):
                return
            stop.set()
            previous.cancel()
        stop = threading.Event()
        thread = threading.Thread(target=_run, args=(key, stop), name="import-dispatch", daemon=True)
        _runners[key] = (thread, stop)
        thread.start()


def stop(database):
    key = str(Path(database).resolve())
    with _lock:
        runner = _runners.pop(key, None)
    if runner:
        runner[1].set()
        if isinstance(runner[0], threading.Timer):
            runner[0].cancel()
        runner[0].join()


def busy(database):
    with _lock:
        runner = _runners.get(str(Path(database).resolve()))
        if runner and runner[0].is_alive() and not isinstance(runner[0], threading.Timer):
            return True
    return job_store.pending(database)


def cancel(database, ids):
    with runtime.write_lock:
        return job_store.cancel(database, ids)
