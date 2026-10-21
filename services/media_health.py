"""检查已入库视频在 AList 中是否仍存在；只标记，不删除任何记录。

扫描目录内的文件在扫描时顺带判断（见 media_discovery），这里补查目录外的文件：
按父目录批量列出，同一目录只请求一次。读取失败时保留原状态，避免误报。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import PurePosixPath

import requests

from connectors import alist
from models import discovery_store as store
from models.database import connect_database
from queries.association import get_associated_paths

MAX_DIRECTORIES = 300


def _missing_error(error: Exception) -> bool:
    text = str(error).casefold()
    return "not found" in text or "不存在" in text or "object not found" in text


def check_paths(database, token, paths: dict[str, str]) -> list[tuple[str, str, str]]:
    """paths: {alist_path: title_id} → [(path, title_id, status)]。"""
    parents = {}
    for path, title_id in paths.items():
        parents.setdefault(str(PurePosixPath(path).parent), []).append((path, title_id))
    password = os.environ.get("ALIST_PATH_PASSWORD", "")

    def check(parent):
        try:
            names = {item.get("name") for item in alist.list_directory(parent, token, path_password=password)}
        except (requests.RequestException, RuntimeError, ValueError) as error:
            if isinstance(error, RuntimeError) and _missing_error(error):
                return [(path, title_id, "missing") for path, title_id in parents[parent]]
            return []
        return [(path, title_id, "ok" if PurePosixPath(path).name in names else "missing")
                for path, title_id in parents[parent]]

    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(check, list(parents)[:MAX_DIRECTORIES]):
            rows.extend(result)
    return rows


def check_outside(database, token, roots: list[str]):
    with closing(connect_database(database)) as db:
        associated = get_associated_paths(db)
    outside = {path: info["title_id"] for path, info in associated.items()
               if path and not any(path.startswith(root + "/") for root in roots)}
    rows = check_paths(database, token, outside)
    if rows:
        store.save_health(database, rows)
    return rows


def missing_by_title(database) -> dict[str, int]:
    """{作品 ID: 失效文件数}，只统计仍在库中的文件。"""
    try:
        missing = store.missing_media(database)
    except Exception:
        return {}
    if not missing:
        return {}
    with closing(connect_database(database)) as db:
        associated = get_associated_paths(db)
    counts = {}
    for row in missing:
        info = associated.get(row["alist_path"])
        if info:
            counts[info["title_id"]] = counts.get(info["title_id"], 0) + 1
    return counts
