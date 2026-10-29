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
"""

_lock = RLock()


def _connect(database=None):
    db = local_store._connect(database or paths.database_path())
    db.executescript(SCHEMA)
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
    }


def list_drives(database=None):
    with closing(_connect(database)) as db:
        rows = db.execute("SELECT * FROM drive ORDER BY sort_order, mount").fetchall()
    return [_row(row) for row in rows]


def get_drive(mount, database=None):
    with closing(_connect(database)) as db:
        row = db.execute("SELECT * FROM drive WHERE mount = ?", (mount,)).fetchone()
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
              datetime.now(timezone.utc).isoformat()))


def update_config(mount, config, database=None):
    with _lock, closing(_connect(database)) as db, db:
        db.execute("UPDATE drive SET config = ? WHERE mount = ?",
                   (json.dumps(config, ensure_ascii=False), mount))


def set_status(mount, status, database=None):
    with _lock, closing(_connect(database)) as db, db:
        db.execute("UPDATE drive SET status = ? WHERE mount = ?", (status, mount))


def delete_drive(mount, database=None):
    with _lock, closing(_connect(database)) as db, db:
        db.execute("DELETE FROM drive WHERE mount = ?", (mount,))
