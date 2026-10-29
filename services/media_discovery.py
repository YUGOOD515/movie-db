"""Progressive, read-only AList discovery. A failed subtree stays visibly incomplete."""

import os
import re
import queue
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime
from pathlib import PurePosixPath
from threading import Lock, Thread

import requests

from models import drive_store
from models import local_store as store
from models.database import connect_database
from queries.association import get_associated_paths
from services import media_source

_lock = Lock()
_scans = {}


def canonical_path(value):
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError("AList 文件路径无效。")
    if any(part in (".", "..", "") for part in value.split("/")[1:]):
        raise ValueError("AList 文件路径无效。")
    return value


def find_share_root():
    configured = (os.environ.get("MEDIA_IMPORT_ROOT") or os.environ.get("ALIST_IMPORT_ROOT", "")).strip().rstrip("/")
    if configured:
        return canonical_path(configured)
    parents = ["/"]
    for _ in range(3):
        following = []
        found = []
        for parent in parents:
            for item in media_source.list_directory(parent):
                if not item.get("is_dir"):
                    continue
                path = str(PurePosixPath(parent) / item["name"])
                if item["name"].replace(":", "：") == "来自：分享":
                    found.append(path)
                following.append(path)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ValueError("发现多个「来自：分享」目录，请配置 MEDIA_IMPORT_ROOT。")
        parents = following[:50]
    raise ValueError("未找到「来自：分享」目录，请配置 MEDIA_IMPORT_ROOT。")


def _timestamp(item):
    for field in ("created", "modified"):
        try:
            value = item.get(field) or ""
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.year > 1970:
                return value, parsed.timestamp(), field
        except (ValueError, TypeError, OSError):
            pass
    return "", 0, "unknown"


def _file(path, item):
    added, sort_time, time_source = _timestamp(item)
    return {"path": path, "name": item["name"], "size": item.get("size", 0),
            "modified": item.get("modified"),
            "added": added, "sort_time": sort_time, "time_source": time_source}


MAX_FOLDERS, MAX_FILES = 1000, 10000


def collect_folder(path, refresh=False, pool=None):
    """遍历 path 下全部视频。传入 pool 时每轮并行读取多个子目录（实际并发受 AList 限速约束），
    大合集不再只占用一个线程逐个目录读取。"""
    files, warnings, pending = [], [], [path]
    visited = set()

    def read(parent):
        try:
            return parent, media_source.list_directory(parent, refresh=refresh)
        except (requests.RequestException, RuntimeError, ValueError):
            return parent, None

    while pending:
        batch = []
        while pending and len(batch) < 8:
            parent = pending.pop()
            if parent in visited:
                continue
            if len(visited) >= MAX_FOLDERS or len(files) >= MAX_FILES:
                warnings.append(f"{path}：达到扫描上限，结果不完整。")
                pending.clear()
                break
            visited.add(parent)
            batch.append(parent)
        results = pool.map(read, batch) if pool is not None and len(batch) > 1 else map(read, batch)
        for parent, entries in results:
            if entries is None:
                warnings.append(f"{parent}：读取失败，请刷新重试。")
                continue
            for item in entries:
                if not item.get("name") or "/" in item["name"] or item["name"] in (".", ".."):
                    continue
                child = str(PurePosixPath(parent) / item["name"])
                if item.get("is_dir"):
                    pending.append(child)
                elif PurePosixPath(child).suffix.lower() in media_source.VIDEO_EXTENSIONS:
                    files.append(_file(child, item))
    return files, warnings


def group_pending(path, entry, files, associated):
    known = [item for key, item in associated.items() if key.startswith(path + "/")]
    pending = [item for item in files if item["path"] not in associated]
    if not pending:
        return []
    if entry.get("is_dir") and not known:
        stamp = max([_file(path, entry), *pending], key=lambda item: item["sort_time"])
        return [{**stamp, "path": path, "name": entry["name"], "kind": "folder",
                 "files": pending, "suggestion": None}]
    records = []
    for item in pending:
        # Use the closest previously associated ancestor, never a sibling prefix.
        parent = PurePosixPath(item["path"]).parent
        context = []
        while str(parent).startswith(path) and str(parent) != "/":
            context = [value for key, value in associated.items() if key.startswith(str(parent) + "/")]
            if context:
                break
            parent = parent.parent
        series = {value["title_id"]: value for value in context}
        suggestion = None
        if len(series) == 1 and next(iter(series.values()))["title_type"] == "series":
            candidate = next(iter(series.values()))
            seasons = {value["season_number"] for value in context}
            suggestion = {"title_id": candidate["title_id"], "name": candidate["name"],
                          "year": candidate["year"], "media_type": "tv",
                          "season_number": next(iter(seasons)) if len(seasons) == 1 else None}
        records.append({**item, "kind": "update" if known else "file",
                        "files": [item], "suggestion": suggestion})
    return records


def split_collection(record):
    """“流浪地球 全2部”这类一个目录多部电影：每个文件片名都不同且没有集号时拆成单独记录。"""
    from services.name_cleaning import folder_hints, has_episode_marker, normalize

    files = record["files"]
    if record["kind"] != "folder" or not 2 <= len(files) <= 30:
        return [record]
    if any(has_episode_marker(item["name"]) for item in files):
        return [record]
    titles = []
    for item in files:
        queries = folder_hints(PurePosixPath(item["name"]).stem)["queries"]
        title = normalize(queries[0]) if queries else ""
        if len(re.sub(r"[0-9]", "", title)) < 2:
            return [record]
        titles.append(re.sub(r"(?:19|20)\d{2}$", "", title))
    if len(set(titles)) != len(titles):
        return [record]
    return [{**item, "kind": "file", "files": [item], "suggestion": None, "collection": record["name"]}
            for item in files]


def scan_roots(database):
    """返回 [(路径, 是否自动识别)]；用户未选择目录时沿用「来自：分享」自动识别。"""
    roots = store.list_roots(database)
    if roots:
        return [(root, False) for root in roots]
    return [(find_share_root(), True)]


def _health_from_scan(associated, roots, listings):
    """扫描已经列出了目录树，借此判断已入库文件是否还在，不额外请求 AList。"""
    rows = []
    for path, info in associated.items():
        for root in roots:
            if not path.startswith(root + "/"):
                continue
            listing = listings.get(root)
            if listing is None:
                break
            entry = path[len(root) + 1:].split("/", 1)[0]
            if entry not in listing["entries"]:
                rows.append((path, info["title_id"], "missing"))
            elif entry in listing["complete"]:
                rows.append((path, info["title_id"], "ok" if path in listing["seen"] else "missing"))
            break
    return rows


def _ai_summary(stats: dict) -> str:
    usage = stats.get("usage") or {}
    if not stats.get("requests"):
        return ""
    text = f"AI 识别 {stats['needs_ai']} 项，采用 {stats['ai']} 项，共 {stats['requests']} 次请求"
    if usage.get("input_tokens"):
        hit = usage.get("cached_tokens", 0) / usage["input_tokens"]
        text += f"，输入 {usage['input_tokens']} token（缓存命中 {hit:.0%}），输出 {usage.get('output_tokens', 0)} token"
    return text


def _recognize_pending(key, database, records=None):
    from services import recognition

    streaming = records is not None
    if not streaming:
        with _lock:
            records = list(_scans[key]["records"])
    cached = store.load_recognitions(database)
    pending = (record for record in records if cached.get(record["path"], {}).get("signature") != recognition.signature(record))
    if not streaming:
        pending = list(pending)
    with _lock:
        _scans[key].update(status=_scans[key]["status"] if streaming else "recognizing", recognized=0,
                           to_recognize=0 if streaming else len(pending),
                           rule_done=0, ai_pending=0, verifying=0)
    if not streaming and not pending:
        return
    try:
        titles = recognition._library_titles(database)
    except Exception:
        titles = []

    def saved(record, result):
        if record.get("incomplete") and result.get("title"):
            result = {**result, "status": "review", "incomplete": True}
        store.save_recognition(database, record["path"],
                               recognition.signature(record, ai=recognition.ai_used(result)), result)

    def progress(counts):
        # 规则、AI、核对三段并行：分别显示各段进度，recognized 为已完成（含失败）的条数。
        with _lock:
            _scans[key].update(recognized=counts["done"], rule_done=counts["rules"],
                               to_recognize=counts["total"],
                               ai_pending=counts["ai"], verifying=counts["verifying"])

    try:
        stats = recognition.recognize_many(database, pending, titles=titles, on_result=saved, on_progress=progress)
    except RuntimeError:
        # TMDB 未配置：停止识别，保留规则清洗后的搜索词。
        return
    with _lock:
        state = _scans[key]
        state.update(ai_pending=0, verifying=0, rule_done=state.get("to_recognize", 0))
        state["ai_summary"] = _ai_summary(stats)
        if stats["deferred"]:
            state["warnings"].append(
                f"AI 接口限流或额度已用尽，{stats['deferred']} 项暂用规则识别结果；恢复后重新扫描会自动补识别。")
        if stats["failed"]:
            state["warnings"].append(
                f"AI 识别失败 {stats['failed']} 项：{stats['error']} 这些项暂用规则结果，重新扫描会再试。")


@contextmanager
def _recognition_pipeline(key, database):
    arrivals = queue.Queue(maxsize=64)

    def run():
        records = iter(arrivals.get, None)
        try:
            _recognize_pending(key, database, records)
        finally:
            # Even a missing TMDB configuration must drain the bounded input so
            # the scanner can finish and report its directory warnings.
            for _ in records:
                pass

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan-recognition") as executor:
        future = executor.submit(run)
        try:
            yield arrivals
        finally:
            arrivals.put(None)
            future.result()


def _run_scan(key, database, refresh):
    try:
        roots = scan_roots(database)
        with _lock:
            _scans[key].update(roots=[{"path": path, "auto": auto} for path, auto in roots],
                               root=roots[0][0] if roots else "")
        listings, plan = {}, []
        for root, _ in roots:
            try:
                entries = media_source.list_directory(root, refresh=refresh)
            except (requests.RequestException, RuntimeError, ValueError):
                with _lock:
                    _scans[key]["warnings"].append(f"{root}：读取失败，请检查目录是否存在。")
                continue
            listings[root] = {"entries": {entry["name"] for entry in entries}, "complete": set(), "seen": set()}
            plan.extend((root, entry) for entry in entries)
        with closing(connect_database(database)) as db:
            associated = get_associated_paths(db)
        ignored = store.ignored_paths(database)
        with _lock:
            _scans[key].update(total=len(plan))

        def scan(root, entry):
            path = str(PurePosixPath(root) / entry["name"])
            if entry.get("is_dir"):
                files, warnings = collect_folder(path, refresh, listing_pool)
            else:
                files = [_file(path, entry)] if PurePosixPath(path).suffix.lower() in media_source.VIDEO_EXTENSIONS else []
                warnings = []
            records = [split for record in group_pending(path, entry, files, associated)
                       for split in split_collection(record)]
            return root, entry["name"], records, warnings, {item["path"] for item in files}

        # 顶层条目 4 路并行；目录读取共用一个池，单个大合集也能并行展开。
        with _recognition_pipeline(key, database) as recognizing, \
                ThreadPoolExecutor(max_workers=4, thread_name_prefix="scan") as pool, \
                ThreadPoolExecutor(max_workers=4, thread_name_prefix="alist-list") as listing_pool:
            for future in as_completed([pool.submit(scan, root, entry) for root, entry in plan]):
                root, name, records, warnings, seen = future.result()
                with _lock:
                    state = _scans[key]
                    for record in records:
                        record["root"] = root
                        record["incomplete"] = bool(warnings)
                        if record["path"] in ignored:
                            state["ignored"].append({"path": record["path"], "name": record["name"],
                                                     "count": len(record["files"])})
                        else:
                            state["records"].append(record)
                    state["warnings"].extend(warnings)
                    state["done"] += 1
                    if not warnings:
                        listings[root]["complete"].add(name)
                    listings[root]["seen"].update(seen)
                for record in records:
                    if record["path"] not in ignored:
                        recognizing.put(record)
            with _lock:
                _scans[key]["status"] = "recognizing"
        health = _health_from_scan(associated, list(listings), listings)
        if health:
            store.save_health(database, health)
        try:
            from services.media_health import check_outside
            check_outside(database, list(listings))
        except Exception:
            pass
        with _lock:
            snapshot = {field: _scans[key][field] for field in ("roots", "root", "records", "ignored", "warnings")}
        store.save_snapshot(database, snapshot)
        with _lock:
            _scans[key]["status"] = "complete"
    except (OSError, requests.RequestException, RuntimeError, ValueError) as error:
        with _lock:
            _scans[key].update(status="error", error=(str(error) if isinstance(error, ValueError)
                else "无法读取 AList，请检查服务、账号和目录权限后重试。"))
    except Exception:
        with _lock:
            _scans[key].update(status="error", error="扫描失败，请检查媒体库及 AList 配置后重试。")


def _empty_state(status):
    return {"status": status, "root": "", "roots": [], "records": [], "ignored": [], "warnings": [],
            "done": 0, "total": 0, "recognized": 0, "to_recognize": 0, "rule_done": 0, "ai_pending": 0, "verifying": 0,
            "ai_summary": ""}


def start_scan(database, refresh=False):
    key = str(database)
    with _lock:
        if _scans.get(key, {}).get("status") in ("running", "recognizing"):
            return False
        _scans[key] = _empty_state("running")
    Thread(target=_run_scan, args=(key, database, refresh), daemon=True).start()
    return True


def _restore(key, database):
    """进程重启后先展示上次扫描结果，后台扫描完成前列表不会空白。"""
    try:
        snapshot = store.load_snapshot(database)
    except Exception:
        snapshot = None
    if not snapshot:
        return None
    state = {**_empty_state("complete"), **snapshot, "restored": True}
    _scans[key] = state
    return state


def scan_status(database, *, prune=True):
    key = str(database)
    with _lock:
        state = _scans.get(key) or _restore(key, database)
        if state is None:
            return _empty_state("idle")
        state = {**_empty_state(state["status"]), **state,
                 "records": list(state["records"]), "warnings": list(state["warnings"]),
                 "ignored": list(state.get("ignored") or [])}
    records = state["records"]
    if prune:
        try:
            with closing(connect_database(database)) as db:
                associated = get_associated_paths(db)
        except Exception:
            associated = {}
        records = []
        for record in state["records"]:
            files = [item for item in record["files"] if item["path"] not in associated]
            if files:
                records.append({**record, "files": files} if len(files) != len(record["files"]) else record)
    if prune:
        try:
            cached = store.load_recognitions(database)
        except Exception:
            cached = {}
        from services.recognition import signature

        merged = []
        originals = {record["path"]: record for record in state["records"]}
        for record in records:
            found = cached.get(record["path"])
            if found and found.get("signature") == signature(originals[record["path"]]):
                found = {name: value for name, value in found.items() if name != "signature"}
                if found.get("positions"):
                    paths = {item["path"] for item in record["files"]}
                    positions = {path: position for path, position in found["positions"].items() if path in paths}
                    found = {**found, "positions": positions}
                    if found.get("coverage") == 1:
                        found["episodes"] = len({tuple(position) for position in positions.values()})
                merged.append({**record, "recognition": found})
            else:
                merged.append(record)
        records = merged
    state["records"] = sorted(records, key=lambda item: (-item["sort_time"], item["path"]))
    if prune:
        from services.media_health import missing_by_title
        state["missing"] = sum(missing_by_title(database).values())
        from connectors import llm
        state["ai"] = llm.describe()
    return state


def scan_summary(database):
    """Observe live counters without loading/pruning the complete discovery list."""
    with _lock:
        state = _scans.get(str(database))
        if not state:
            return {"status": "idle"}
        summary = {key: state.get(key) for key in ("status", "done", "total", "recognized", "to_recognize", "ai_pending", "verifying", "error")}
        summary["warnings"] = list(state.get("warnings") or [])[:5]
        return summary


def set_ignored(database, path, name, ignored):
    canonical_path(path)
    store.set_ignored(database, path, name, ignored)
    with _lock:
        state = _scans.get(str(database))
        if not state:
            return
        if ignored:
            moved = [record for record in state["records"] if record["path"] == path]
            state["records"] = [record for record in state["records"] if record["path"] != path]
            state.setdefault("ignored", []).extend(
                {"path": record["path"], "name": record["name"], "count": len(record["files"])} for record in moved)
        else:
            state["ignored"] = [item for item in state.get("ignored") or [] if item["path"] != path]


def find_record(database, path):
    for record in scan_status(database, prune=False)["records"]:
        if record["path"] == path:
            return record
    return None


def browse(path):
    path = canonical_path(path) if path != "/" else "/"
    entries = media_source.list_directory(path)
    folders = sorted((item["name"] for item in entries if item.get("is_dir") and item.get("name")),
                     key=lambda name: name.casefold())
    parent = str(PurePosixPath(path).parent) if path != "/" else None
    return {"path": path, "parent": parent,
            "folders": [{"name": name, "path": str(PurePosixPath(path) / name)} for name in folders[:500]]}


_autoscan_started = set()


def start_autoscan(database, *, attempts=24, delay=5):
    """启动后在后台扫描一次：等网盘就绪后扫描、检查失效文件并预识别。"""
    import time

    key = str(database)
    if key in _autoscan_started or os.environ.get("MOVIEDB_AUTOSCAN", "1").strip() == "0":
        return False
    if not any(row["enabled"] for row in drive_store.list_drives()):
        return False
    _autoscan_started.add(key)

    def wait_and_scan():
        for _ in range(attempts):
            try:
                media_source.list_directory("/")
            except (requests.RequestException, RuntimeError, ValueError):
                time.sleep(delay)
                continue
            start_scan(database)
            return

    Thread(target=wait_and_scan, daemon=True).start()
    return True
