"""读取 AList / OpenList 的存储配置，只读，不改动原文件。

支持两种来源：
- 数据目录下的 data.db：存储在 x_storages 表（表前缀可在 AList 配置里修改，按后缀匹配）；
- 后台“备份与恢复”导出的 JSON：顶层 storages 数组。

每个存储返回 {"mount_path", "driver", "addition"(dict), "disabled"}；addition 是驱动自己的字段，
字段名与 AList 源码 drivers/<驱动>/meta.go 中的 json 标签一致。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

MAX_JSON_BYTES = 20 * 1024 * 1024

# 常见的数据目录位置，用于在导入弹窗里预填路径
CANDIDATES = (
    "C:/alist/data/data.db",
    "C:/openlist/data/data.db",
    "D:/alist/data/data.db",
    "~/alist/data/data.db",
    "~/openlist/data/data.db",
    "/opt/alist/data/data.db",
    "/opt/openlist/data/data.db",
)


def guess_path() -> str:
    for candidate in CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return ""


def read_storages(location: str) -> list[dict]:
    path = Path(str(location or "").strip().strip('"')).expanduser()
    if not str(location or "").strip():
        raise ValueError("请填写 AList 的 data.db 或备份 JSON 的路径。")
    if not path.is_file():
        raise ValueError("找不到这个文件，请检查路径。")
    if path.suffix.lower() == ".json":
        return _from_json(path)
    return _from_database(path)


def _from_database(path: Path) -> list[dict]:
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%storages' ORDER BY length(name)"
            ).fetchone()
            if table is None:
                raise ValueError("这不是 AList / OpenList 的数据库：没有找到存储表。")
            rows = [dict(row) for row in db.execute(f'SELECT * FROM "{table["name"]}"')]
    except sqlite3.DatabaseError:
        raise ValueError("无法读取这个文件：不是有效的 SQLite 数据库。") from None
    rows.sort(key=lambda row: (row.get("order") or 0, row.get("id") or 0))
    return [_storage(row) for row in rows]


def _from_json(path: Path) -> list[dict]:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("备份文件过大。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("无法读取这个文件：不是有效的 JSON。") from None
    storages = payload.get("storages") if isinstance(payload, dict) else payload
    if not isinstance(storages, list):
        raise ValueError("备份文件里没有存储配置（storages）。")
    return [_storage(item) for item in storages if isinstance(item, dict)]


def _storage(row: dict) -> dict:
    addition = row.get("addition") or {}
    if isinstance(addition, str):
        try:
            addition = json.loads(addition or "{}")
        except json.JSONDecodeError:
            addition = {}
    return {
        "mount_path": str(row.get("mount_path") or ""),
        "driver": str(row.get("driver") or ""),
        "addition": addition if isinstance(addition, dict) else {},
        "disabled": bool(row.get("disabled")),
    }
