"""Progressive, read-only drive discovery. A failed subtree stays visibly incomplete."""

import logging
import os
import re
import queue
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime
from pathlib import PurePosixPath
from threading import Event, Lock, Thread

import requests

from connectors import throttle
from models import drive_store
from models import local_store as store
from models.database import connect_database
from queries.association import get_associated_paths
from services import media_source

logger = logging.getLogger(__name__)

_lock = Lock()
_scans = {}
_cancels: dict[int, Event] = {}


class _ScanCancelled(Exception):
    pass


def canonical_path(value):
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError("文件路径无效。")
    if any(part in (".", "..", "") for part in value.split("/")[1:]):
        raise ValueError("文件路径无效。")
    return value


def find_share_root(cancel: Event | None = None):
    configured = (os.environ.get("MEDIA_IMPORT_ROOT") or os.environ.get("ALIST_IMPORT_ROOT", "")).strip().rstrip("/")
    if configured:
        return canonical_path(configured)
    parents = ["/"]
    for _ in range(3):
        if cancel is not None and cancel.is_set():
            raise _ScanCancelled
        following = []
        found = []
        for parent in parents:
            if cancel is not None and cancel.is_set():
                raise _ScanCancelled
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
            raise ValueError("找到多个「来自：分享」目录，请点“管理扫描目录”选择要扫描的目录。")
        parents = following[:50]
    # 面向界面的提示：新用户还没选目录时最常见，不提 .env 里的 MEDIA_IMPORT_ROOT
    raise ValueError("还没有选择扫描目录，请点“管理扫描目录”选择存放影视文件的目录。")


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
# 顶层目录一个视频都没有、却列了这么久（秒）：扫描结束后提示用户以后跳过
SLOW_EMPTY_SECONDS = 10


def collect_folder(path, refresh=False, pool=None, skip=(), skipped=None, cancel: Event | None = None):
    """遍历 path 下全部视频。传入 pool 时每轮并行读取多个子目录（实际并发受 AList 限速约束），
    大合集不再只占用一个线程逐个目录读取。skip 里的目录（已忽略）不再展开，路径记入 skipped。"""
    files, warnings, pending = [], [], [path]
    visited = set()

    def read(parent):
        if cancel is not None and cancel.is_set():
            return parent, []
        try:
            return parent, media_source.list_directory(parent, refresh=refresh)
        except (requests.RequestException, RuntimeError, ValueError, FileNotFoundError):
            # 目录在两次扫描之间被删除或改名：只记这一处读取失败，不让整次扫描报错
            return parent, None

    while pending:
        if cancel is not None and cancel.is_set():
            return files, warnings
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
                if item.get("is_dir") and child in skip:
                    if skipped is not None:
                        skipped.append(child)
                elif item.get("is_dir"):
                    pending.append(child)
                elif PurePosixPath(child).suffix.lower() in media_source.VIDEO_EXTENSIONS:
                    files.append(_file(child, item))
    return files, warnings


def group_pending(path, entry, files, associated):
    from services.media_grouping import partition

    groups = partition(path, files) if entry.get("is_dir") else [
        {**item, "kind": "file", "files": [item], "suggestion": None} for item in files]
    records = []
    for group in groups:
        pending = [item for item in group["files"] if item["path"] not in associated]
        if not pending:
            continue
        boundary = group["path"]
        known = [value for key, value in associated.items() if key.startswith(boundary + "/")]
        if not known:
            records.append({**group, "files": pending})
            continue
        for item in pending:
            parent, context = PurePosixPath(item["path"]).parent, []
            while str(parent) == boundary or str(parent).startswith(boundary + "/"):
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
            records.append({**item, "kind": "update", "files": [item], "suggestion": suggestion,
                            "context_path": boundary})
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


def scan_roots(database, cancel: Event | None = None):
    """返回 [(路径, 是否自动识别)]；用户未选择目录时沿用「来自：分享」自动识别。"""
    roots = store.list_roots(database)
    if roots:
        return [(root, False) for root in roots]
    return [(find_share_root(cancel=cancel), True)]


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


def _recognize_pending(key, database, records=None, state=None, cancel: Event | None = None):
    from services import recognition

    cancelled = lambda: cancel is not None and cancel.is_set()
    streaming = records is not None
    with _lock:
        target = state if state is not None else _scans[key]
        if not streaming:
            records = list(target["records"])
    if cancelled():
        return
    cached = store.load_recognitions(database)
    pending = (record for record in records if cached.get(record["path"], {}).get("signature") != recognition.signature(record))
    if not streaming:
        pending = list(pending)
    with _lock:
        if not cancelled():
            target.update(status=target["status"] if streaming else "recognizing", recognized=0,
                          to_recognize=0 if streaming else len(pending),
                          rule_done=0, ai_pending=0, verifying=0)
    if not streaming and not pending:
        return
    try:
        titles = recognition._library_titles(database)
    except Exception:
        titles = []

    def saved(record, result):
        if cancelled():
            return
        if record.get("incomplete") and result.get("title"):
            result = {**result, "status": "review", "incomplete": True}
        store.save_recognition(database, record["path"],
                               recognition.signature(record, ai=recognition.ai_used(result)), result)

    def progress(counts):
        # 规则、AI、核对三段并行：分别显示各段进度，recognized 为已完成（含失败）的条数。
        with _lock:
            if not cancelled():
                target.update(recognized=counts["done"], rule_done=counts["rules"],
                              to_recognize=counts["total"],
                              ai_pending=counts["ai"], verifying=counts["verifying"])

    try:
        stats = recognition.recognize_many(database, pending, titles=titles, on_result=saved, on_progress=progress, cancel=cancel)
    except RuntimeError:
        # TMDB 未配置：停止识别，保留规则清洗后的搜索词。
        return
    if cancelled():
        return
    with _lock:
        if cancelled():
            return
        target.update(ai_pending=0, verifying=0, rule_done=target.get("to_recognize", 0))
        target["ai_summary"] = _ai_summary(stats)
        if stats["deferred"]:
            target["warnings"].append(
                f"AI 接口限流或额度已用尽，{stats['deferred']} 项暂用规则识别结果；恢复后重新扫描会自动补识别。")
        if stats["failed"]:
            target["warnings"].append(
                f"AI 识别失败 {stats['failed']} 项：{stats['error']} 这些项暂用规则结果，重新扫描会再试。")
        if stats.get("offline"):
            target["warnings"].append(
                f"暂时连不上 TMDB，{stats['offline']} 项未能识别。请检查网络，或在设置里填写代理后重新扫描。")


@contextmanager
def _recognition_pipeline(key, database, state=None, cancel: Event | None = None):
    arrivals = queue.Queue(maxsize=64)

    def run():
        records = iter(arrivals.get, None)
        try:
            _recognize_pending(key, database, records, state=state, cancel=cancel)
        except Exception:
            # 识别出了意料之外的错（例如接口返回结构异常）：目录已经扫完，保留扫描结果，
            # 只提示部分条目未识别，下次扫描会重试，不让整次扫描变成“失败”
            logger.exception("扫描中的识别出错")
            with _lock:
                target = state if state is not None else _scans.get(key)
                if target is not None and not (cancel is not None and cancel.is_set()):
                    target["warnings"].append("识别中途出错，部分条目未识别；重新扫描会再试。")
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
    with _lock:
        run_state = _scans[key]
        cancel = _cancels.setdefault(id(run_state), Event())
    try:
        roots = scan_roots(database, cancel=cancel)
        if cancel.is_set():
            raise _ScanCancelled
        with _lock:
            if cancel.is_set():
                raise _ScanCancelled
            run_state.update(roots=[{"path": path, "auto": auto} for path, auto in roots],
                             root=roots[0][0] if roots else "")
        listings, plan = {}, []
        for root, _ in roots:
            if cancel.is_set():
                raise _ScanCancelled
            try:
                entries = media_source.list_directory(root, refresh=refresh)
            except (requests.RequestException, RuntimeError, ValueError, FileNotFoundError):
                with _lock:
                    if not cancel.is_set():
                        run_state["warnings"].append(f"{root}：读取失败，请检查目录是否存在。")
                continue
            listings[root] = {"entries": {entry["name"] for entry in entries}, "complete": set(), "seen": set()}
            plan.extend((root, entry) for entry in entries)
        with closing(connect_database(database)) as db:
            associated = get_associated_paths(db)
        ignored = store.ignored_paths(database)
        with _lock:
            if cancel.is_set():
                raise _ScanCancelled
            run_state.update(total=len(plan))

        def scan(root, entry):
            if cancel.is_set():
                return root, entry["name"], [], [], set(), True, None
            path = str(PurePosixPath(root) / entry["name"])
            started, skipped = time.monotonic(), []
            if entry.get("is_dir") and path in ignored:
                # 已忽略的目录整棵跳过，不再向网盘列目录
                files, warnings, skipped = [], [], [path]
            elif entry.get("is_dir"):
                files, warnings = collect_folder(path, refresh, listing_pool, ignored, skipped, cancel=cancel)
            else:
                files = [_file(path, entry)] if PurePosixPath(path).suffix.lower() in media_source.VIDEO_EXTENSIONS else []
                warnings = []
            if cancel.is_set():
                return root, entry["name"], [], [], set(), True, None
            visible, hidden = [], {}
            for item in files:
                owner = next((ignored_path for ignored_path in sorted(ignored, key=len, reverse=True)
                              if item["path"] == ignored_path or item["path"].startswith(ignored_path + "/")), None)
                if owner:
                    hidden[owner] = hidden.get(owner, 0) + 1
                else:
                    visible.append(item)
            records = group_pending(path, entry, visible, associated)
            if entry.get("is_dir") and not warnings and not cancel.is_set():
                from services.recognition_harness import plan_groups
                records = plan_groups(database, path, records)
            records.extend({"path": owner, "name": ignored[owner], "files": [None] * count,
                            "_ignored": True} for owner, count in hidden.items())
            # 跳过的目录没有列出，不知道里面有几个视频
            records.extend({"path": owner, "name": ignored[owner], "files": [], "_ignored": True}
                           for owner in skipped if owner not in hidden)
            seconds = time.monotonic() - started
            slow = {"path": path, "name": entry["name"], "seconds": round(seconds)} if (
                entry.get("is_dir") and not files and not warnings and not skipped
                and seconds >= SLOW_EMPTY_SECONDS) else None
            return root, entry["name"], records, warnings, {item["path"] for item in files}, bool(skipped), slow

        # 顶层条目 4 路并行；目录读取共用一个池，单个大合集也能并行展开。
        with _recognition_pipeline(key, database, state=run_state, cancel=cancel) as recognizing, \
                ThreadPoolExecutor(max_workers=4, thread_name_prefix="scan") as pool, \
                ThreadPoolExecutor(max_workers=4, thread_name_prefix="alist-list") as listing_pool:
            for future in as_completed([pool.submit(scan, root, entry) for root, entry in plan]):
                root, name, records, warnings, seen, partial, slow = future.result()
                if cancel.is_set():
                    continue
                with _lock:
                    if cancel.is_set():
                        continue
                    for record in records:
                        record["root"] = root
                        record["incomplete"] = bool(warnings)
                        if record.get("_ignored"):
                            run_state["ignored"].append({"path": record["path"], "name": record["name"],
                                                         "count": len(record["files"])})
                        else:
                            run_state["records"].append(record)
                    run_state["warnings"].extend(warnings)
                    run_state["done"] += 1
                    if slow:
                        run_state["slow_empty"].append(slow)
                    # 跳过了子目录就不算完整列出，免得把里面已入库的视频判成失效
                    if not warnings and not partial:
                        listings[root]["complete"].add(name)
                    listings[root]["seen"].update(seen)
                for record in records:
                    if cancel.is_set():
                        break
                    if not record.get("_ignored"):
                        recognizing.put(record)
            with _lock:
                if not cancel.is_set():
                    run_state["status"] = "recognizing"
        if cancel.is_set():
            raise _ScanCancelled
        health = _health_from_scan(associated, list(listings), listings)
        if health:
            store.save_health(database, health)
        try:
            from services.media_health import check_outside
            check_outside(database, list(listings))
        except Exception:
            pass
        with _lock:
            if cancel.is_set():
                raise _ScanCancelled
            snapshot = {field: run_state[field] for field in ("roots", "root", "records", "ignored", "warnings", "slow_empty")}
        store.save_snapshot(database, snapshot)
        with _lock:
            if not cancel.is_set():
                run_state["status"] = "complete"
    except _ScanCancelled:
        with _lock:
            run_state["status"] = "cancelled"
    except (OSError, requests.RequestException, RuntimeError, ValueError) as error:
        with _lock:
            if not cancel.is_set():
                run_state.update(status="error", error=(str(error) if isinstance(error, ValueError)
                    else "无法读取网盘，请检查挂载配置、账号和目录权限后重试。"))
    except Exception:
        with _lock:
            if not cancel.is_set():
                run_state.update(status="error", error="扫描失败，请检查媒体库及网盘配置后重试。")
    finally:
        with _lock:
            _cancels.pop(id(run_state), None)


def _empty_state(status):
    return {"status": status, "root": "", "roots": [], "records": [], "ignored": [], "warnings": [], "slow_empty": [],
            "done": 0, "total": 0, "recognized": 0, "to_recognize": 0, "rule_done": 0, "ai_pending": 0, "verifying": 0,
            "ai_summary": ""}


def cancel_scan(database) -> bool:
    """停止正在进行的扫描与识别，不再排队续扫。"""
    key = str(database)
    with _lock:
        current = _scans.get(key)
        if not current or current.get("status") not in ("running", "recognizing"):
            return False
        _cancels.setdefault(id(current), Event()).set()
        current.pop("rescan", None)
        current.pop("rescan_refresh", None)
        current["status"] = "cancelled"
        return True


def start_scan(database, refresh=False):
    """开始扫描。已有扫描进行中时（例如刚添加了目录）不并发，记下“待重新扫描”，当前这次结束后自动再扫。"""
    key = str(database)
    with _lock:
        current = _scans.get(key)
        if current and current.get("status") in ("running", "recognizing"):
            current["rescan"] = True
            current["rescan_refresh"] = bool(current.get("rescan_refresh")) or refresh
            return False
        run_state = _empty_state("running")
        _scans[key] = run_state
    Thread(target=_scan_then_rescan, args=(key, database, refresh), daemon=True).start()
    return True


def _scan_then_rescan(key, database, refresh):
    with _lock:
        run_state = _scans.get(key)
    try:
        _run_scan(key, database, refresh)
    finally:
        with _lock:
            state = run_state if run_state is not None else (_scans.get(key) or {})
            again, refresh_again = state.pop("rescan", False), state.pop("rescan_refresh", False)
        if again:
            start_scan(database, refresh_again)


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
                 "ignored": list(state.get("ignored") or []), "slow_empty": list(state.get("slow_empty") or [])}
    state["rescan"] = bool(state.get("rescan"))
    # 目录列表以已保存的配置为准：刚添加 / 移除的目录立即可见，扫描结果在下一轮扫描后更新
    try:
        configured = store.list_roots(database)
    except Exception:
        configured = []
    if configured:
        state["roots"] = [{"path": path, "auto": False} for path in configured]
    records = state["records"]
    if prune:
        try:
            with closing(connect_database(database)) as db:
                associated = get_associated_paths(db)
        except Exception:
            associated = None
        records = []
        for record in state["records"]:
            files = [item for item in record["files"] if item["path"] not in (associated or {})]
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
            if found and found.get("signature") in (
                    signature(originals[record["path"]]), signature(originals[record["path"]], ai=False)):
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
        state["missing"] = sum(missing_by_title(database, associated).values())
        from connectors import llm
        state["ai"] = llm.describe()
        state["services"] = throttle.diagnostics()
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
            # 没有视频的慢目录不在待关联列表里，也要立刻出现在“已忽略”中
            if not moved and all(item["path"] != path for item in state["ignored"]):
                state["ignored"].append({"path": path, "name": name, "count": 0})
            state["slow_empty"] = [item for item in state.get("slow_empty") or [] if item["path"] != path]
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


def _has_scan_scope(database) -> bool:
    """新用户还没选扫描目录且库里没有任何作品时，不在后台盲目翻找「来自：分享」。"""
    if (os.environ.get("MEDIA_IMPORT_ROOT") or os.environ.get("ALIST_IMPORT_ROOT", "")).strip():
        return True
    try:
        if store.list_roots(database):
            return True
    except Exception:
        pass
    try:
        with closing(connect_database(database)) as db:
            return bool(get_associated_paths(db))
    except Exception:
        return False


def start_autoscan(database, *, attempts=24, delay=5):
    """启动后在后台扫描一次：等网盘就绪后扫描、检查失效文件并预识别。"""
    import time

    key = str(database)
    if key in _autoscan_started or os.environ.get("MOVIEDB_AUTOSCAN", "1").strip() == "0":
        return False
    if not any(row["enabled"] for row in drive_store.list_drives()):
        return False
    if not _has_scan_scope(database):
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
