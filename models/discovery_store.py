"""扫描目录、识别结果、忽略列表与源文件健康状态的本地存储。

这些数据只服务于本机的“待关联”流程，单独放在 ``discovery.db``，
不写入 ``library.db``，避免触发云端快照同步。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from threading import RLock

_lock = RLock()
_ready = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_root (
    path TEXT PRIMARY KEY,
    added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL,
    finished_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS recognition (
    record_path TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ignored_path (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ignored_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS name_alias (
    alias TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lookup_cache (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS media_health (
    alist_path TEXT PRIMARY KEY,
    title_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'missing')),
    checked_at REAL NOT NULL
);
"""


def store_path(library_database) -> Path:
    configured = os.environ.get("MOVIEDB_DISCOVERY_DATABASE", "").strip()
    return Path(configured) if configured else Path(library_database).resolve().with_name("discovery.db")


def _connect(library_database) -> sqlite3.Connection:
    path = store_path(library_database)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    key = str(path)
    if key not in _ready:
        with _lock:
            connection.executescript(SCHEMA)
            _ready.add(key)
    return connection


def reset():
    """数据文件被删除后调用：下次连接时重新建表。"""
    with _lock:
        _ready.clear()


def _write(library_database, statements):
    with _lock, closing(_connect(library_database)) as db, db:
        for sql, params in statements:
            if isinstance(params, list):
                db.executemany(sql, params)
            else:
                db.execute(sql, params)


# 扫描目录 ---------------------------------------------------------------

def list_roots(library_database) -> list[str]:
    with closing(_connect(library_database)) as db:
        return [row["path"] for row in db.execute("SELECT path FROM scan_root ORDER BY added_at, path")]


def add_root(library_database, path: str):
    _write(library_database, [("INSERT OR IGNORE INTO scan_root (path, added_at) VALUES (?, ?)", (path, time.time()))])


def remove_root(library_database, path: str):
    _write(library_database, [("DELETE FROM scan_root WHERE path = ?", (path,))])


# 扫描快照 ---------------------------------------------------------------

def save_snapshot(library_database, payload: dict):
    _write(library_database, [(
        "INSERT INTO scan_snapshot (id, payload, finished_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, finished_at = excluded.finished_at",
        (json.dumps(payload, ensure_ascii=False), time.time()),
    )])


def load_snapshot(library_database) -> dict | None:
    with closing(_connect(library_database)) as db:
        row = db.execute("SELECT payload, finished_at FROM scan_snapshot WHERE id = 1").fetchone()
    if row is None:
        return None
    return {**json.loads(row["payload"]), "finished_at": row["finished_at"]}


# 识别结果 ---------------------------------------------------------------

def load_recognitions(library_database) -> dict[str, dict]:
    with closing(_connect(library_database)) as db:
        rows = db.execute("SELECT record_path, signature, payload FROM recognition").fetchall()
    return {row["record_path"]: {**json.loads(row["payload"]), "signature": row["signature"]} for row in rows}


def save_recognition(library_database, record_path: str, signature: str, payload: dict):
    _write(library_database, [(
        "INSERT INTO recognition (record_path, signature, payload, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(record_path) DO UPDATE SET signature = excluded.signature, "
        "payload = excluded.payload, updated_at = excluded.updated_at",
        (record_path, signature, json.dumps(payload, ensure_ascii=False), time.time()),
    )])


def forget_recognition(library_database, record_path: str):
    _write(library_database, [("DELETE FROM recognition WHERE record_path = ?", (record_path,))])


# 忽略列表 ---------------------------------------------------------------

def ignored_paths(library_database) -> dict[str, str]:
    with closing(_connect(library_database)) as db:
        return {row["path"]: row["name"] for row in db.execute("SELECT path, name FROM ignored_path ORDER BY ignored_at DESC")}


def set_ignored(library_database, path: str, name: str, ignored: bool):
    if ignored:
        _write(library_database, [("INSERT OR REPLACE INTO ignored_path (path, name, ignored_at) VALUES (?, ?, ?)",
                                   (path, name, time.time()))])
    else:
        _write(library_database, [("DELETE FROM ignored_path WHERE path = ?", (path,))])


# 名称别名（人工确认后学习）---------------------------------------------

def find_alias(library_database, alias: str) -> dict | None:
    with closing(_connect(library_database)) as db:
        row = db.execute("SELECT payload FROM name_alias WHERE alias = ?", (alias,)).fetchone()
    return json.loads(row["payload"]) if row else None


def save_alias(library_database, alias: str, title: dict):
    _write(library_database, [(
        "INSERT OR REPLACE INTO name_alias (alias, payload, updated_at) VALUES (?, ?, ?)",
        (alias, json.dumps(title, ensure_ascii=False), time.time()),
    )])


# 源文件健康状态 ---------------------------------------------------------

def save_health(library_database, rows: list[tuple[str, str, str]]):
    """rows: [(alist_path, title_id, status)]，status 为 ok 或 missing。"""
    now = time.time()
    _write(library_database, [(
        "INSERT OR REPLACE INTO media_health (alist_path, title_id, status, checked_at) VALUES (?, ?, ?, ?)",
        [(path, title_id, status, now) for path, title_id, status in rows],
    )])


def missing_media(library_database) -> list[dict]:
    with closing(_connect(library_database)) as db:
        return [dict(row) for row in db.execute(
            "SELECT alist_path, title_id, checked_at FROM media_health WHERE status = 'missing' ORDER BY alist_path")]


# TMDB 查询缓存（搜索结果、作品摘要）-------------------------------------

def cache_get(library_database, key: str):
    with closing(_connect(library_database)) as db:
        row = db.execute("SELECT payload FROM lookup_cache WHERE key = ? AND expires_at > ?", (key, time.time())).fetchone()
    return json.loads(row["payload"]) if row else None


def cache_put(library_database, key: str, value, ttl: float):
    now = time.time()
    statements = [("INSERT OR REPLACE INTO lookup_cache (key, payload, expires_at) VALUES (?, ?, ?)",
                   (key, json.dumps(value, ensure_ascii=False), now + ttl))]
    if hash(key) % 200 == 0:  # 偶尔顺手清掉过期项，避免表无限增长
        statements.append(("DELETE FROM lookup_cache WHERE expires_at <= ?", (now,)))
    _write(library_database, statements)
