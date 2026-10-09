"""Progressive, read-only AList discovery. A failed subtree stays visibly incomplete."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime
from pathlib import PurePosixPath
from threading import Lock, Thread

import requests

from connectors import alist
from models.database import connect_database
from queries.association import get_associated_paths

_lock = Lock()
_scans = {}


def canonical_path(value):
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError("AList 文件路径无效。")
    if any(part in (".", "..", "") for part in value.split("/")[1:]):
        raise ValueError("AList 文件路径无效。")
    return value


def find_share_root(token):
    configured = os.environ.get("ALIST_IMPORT_ROOT", "").strip().rstrip("/")
    if configured:
        return canonical_path(configured)
    parents = ["/"]
    for _ in range(3):
        following = []
        found = []
        for parent in parents:
            for item in alist.list_directory(parent, token):
                if not item.get("is_dir"):
                    continue
                path = str(PurePosixPath(parent) / item["name"])
                if item["name"].replace(":", "：") == "来自：分享":
                    found.append(path)
                following.append(path)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ValueError("发现多个「来自：分享」目录，请配置 ALIST_IMPORT_ROOT。")
        parents = following[:50]
    raise ValueError("未找到「来自：分享」目录，请配置 ALIST_IMPORT_ROOT。")


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
            "added": added, "sort_time": sort_time, "time_source": time_source}


def collect_folder(path, token, refresh=False):
    files, warnings, pending = [], [], [path]
    visited = set()
    while pending:
        parent = pending.pop()
        if parent in visited:
            continue
        if len(visited) >= 1000 or len(files) >= 10000:
            warnings.append(f"{path}：达到扫描上限，结果不完整。")
            break
        visited.add(parent)
        try:
            entries = alist.list_directory(parent, token,
                path_password=os.environ.get("ALIST_PATH_PASSWORD", ""), refresh=refresh)
        except (requests.RequestException, RuntimeError, ValueError):
            warnings.append(f"{parent}：读取失败，请刷新重试。")
            continue
        for item in entries:
            if not item.get("name") or "/" in item["name"] or item["name"] in (".", ".."):
                continue
            child = str(PurePosixPath(parent) / item["name"])
            if item.get("is_dir"):
                pending.append(child)
            elif PurePosixPath(child).suffix.lower() in alist.VIDEO_EXTENSIONS:
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


def _run_scan(key, database, refresh):
    try:
        token = alist.configured_token()
        root = find_share_root(token)
        entries = alist.list_directory(root, token,
            path_password=os.environ.get("ALIST_PATH_PASSWORD", ""), refresh=refresh)
        with closing(connect_database(database)) as db:
            associated = get_associated_paths(db)
        with _lock:
            _scans[key].update(root=root, total=len(entries))
        def scan(entry):
            path = str(PurePosixPath(root) / entry["name"])
            if entry.get("is_dir"):
                files, warnings = collect_folder(path, token, refresh)
            else:
                files = [_file(path, entry)] if PurePosixPath(path).suffix.lower() in alist.VIDEO_EXTENSIONS else []
                warnings = []
            return group_pending(path, entry, files, associated), warnings
        with ThreadPoolExecutor(max_workers=4) as pool:
            for future in as_completed([pool.submit(scan, entry) for entry in entries]):
                records, warnings = future.result()
                with _lock:
                    state = _scans[key]
                    state["records"].extend(records)
                    state["warnings"].extend(warnings)
                    state["done"] += 1
        with _lock:
            _scans[key]["status"] = "complete"
    except (OSError, requests.RequestException, RuntimeError, ValueError) as error:
        with _lock:
            _scans[key].update(status="error", error=(str(error) if isinstance(error, ValueError)
                else "无法读取 AList，请检查服务、账号和目录权限后重试。"))
    except Exception:
        with _lock:
            _scans[key].update(status="error", error="扫描失败，请检查媒体库及 AList 配置后重试。")


def start_scan(database, refresh=False):
    key = str(database)
    with _lock:
        if _scans.get(key, {}).get("status") == "running":
            return
        _scans[key] = {"status": "running", "root": "", "records": [], "warnings": [], "done": 0, "total": 0}
    Thread(target=_run_scan, args=(key, database, refresh), daemon=True).start()


def scan_status(database):
    with _lock:
        state = _scans.get(str(database))
        if state is None:
            return {"status": "idle", "records": [], "warnings": []}
        return {**state, "records": sorted(state["records"], key=lambda item: (-item["sort_time"], item["path"])),
                "warnings": list(state["warnings"])}
