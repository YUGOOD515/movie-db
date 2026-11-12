from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from threading import RLock

from models import local_store
from services import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS drive (
    mount TEXT NOT NULL PRIMARY KEY,
    kind TEXT NOT NULL,
    config TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
-- 路由：同名挂载是否改由透传（"*"）提供。没有记录即由挂载自己提供；停用时文件不可用，不再悄悄回退透传。
CREATE TABLE IF NOT EXISTS drive_route (
    mount TEXT NOT NULL PRIMARY KEY,
    delegated INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

# 旧版本停用挂载后会自动回退到透传；首次建路由表时把这种现状登记为“改由透传提供”，行为不变。
MIGRATE_ROUTES = """
INSERT OR IGNORE INTO drive_route (mount, delegated, updated_at)
SELECT drive.mount, 1, ?
  FROM drive
 WHERE drive.enabled = 0
   AND drive.mount != '*'
   AND EXISTS (SELECT 1 FROM drive AS passthrough WHERE passthrough.mount = '*' AND passthrough.enabled = 1)
"""

SELECT_DRIVES = """
SELECT drive.*, COALESCE(route.delegated, 0) AS delegated
  FROM drive
  LEFT JOIN drive_route AS route
    ON route.mount = drive.mount
"""

_lock = RLock()
_routes_ready: set[str] = set()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(database=None):
    database = database or paths.database_path()
    db = local_store._connect(database)
    key = str(local_store.store_path(database))
    if key not in _routes_ready:
        with _lock:
            existed = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'drive_route'").fetchone()
            db.executescript(SCHEMA)
            if not existed:
                with db:
                    db.execute(MIGRATE_ROUTES, (_now(),))
            _routes_ready.add(key)
    return db


def _row(row):
    return {
        "mount": row["mount"],
        "kind": row["kind"],
        "config": json.loads(row["config"]),
        "enabled": bool(row["enabled"]),
        "status": row["status"],
        "sort_order": row["sort_order"],
        "updated_at": row["updated_at"],
        "delegated": bool(row["delegated"]) and row["mount"] != "*",
    }


def list_drives(database=None):
    with closing(_connect(database)) as db:
        rows = db.execute(SELECT_DRIVES + " ORDER BY drive.sort_order, drive.mount").fetchall()
    return [_row(row) for row in rows]


def get_drive(mount, database=None):
    with closing(_connect(database)) as db:
        row = db.execute(SELECT_DRIVES + " WHERE drive.mount = ?", (mount,)).fetchone()
    return _row(row) if row else None


def save_drive(mount, kind, config, enabled, database=None):
    if not mount or "/" in mount or mount in (".", "..") or (mount == "*") != (kind == "alist"):
        raise ValueError("挂载名无效。")
    with _lock, closing(_connect(database)) as db, db:
        db.execute("""
            INSERT INTO drive (mount, kind, config, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mount) DO UPDATE SET kind = excluded.kind, config = excluded.config,
                enabled = excluded.enabled, updated_at = excluded.updated_at
        """, (mount, kind, json.dumps(config, ensure_ascii=False), int(bool(enabled)),
              _now()))


def update_config(mount, config, database=None):
    with _lock, closing(_connect(database)) as db, db:
        db.execute("UPDATE drive SET config = ? WHERE mount = ?",
                   (json.dumps(config, ensure_ascii=False), mount))


def set_status(mount, status, database=None):
    with _lock, closing(_connect(database)) as db, db:
        db.execute("UPDATE drive SET status = ? WHERE mount = ?", (status, mount))


def set_delegated(mount, delegated, database=None):
    """同名挂载改由透传提供（True），或改回由挂载自己提供（False）。"""
    with _lock, closing(_connect(database)) as db, db:
        if delegated:
            db.execute("""
                INSERT INTO drive_route (mount, delegated, updated_at) VALUES (?, 1, ?)
                ON CONFLICT(mount) DO UPDATE SET delegated = 1, updated_at = excluded.updated_at
            """, (mount, _now()))
        else:
            db.execute("DELETE FROM drive_route WHERE mount = ?", (mount,))


def delete_drive(mount, database=None):
    with _lock, closing(_connect(database)) as db, db:
        db.execute("DELETE FROM drive WHERE mount = ?", (mount,))
        db.execute("DELETE FROM drive_route WHERE mount = ?", (mount,))
