"""Persistent request budgets and response cache; credentials are only hashed."""

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from connectors.throttle import RateLimitError
from services import paths


def database_path():
    return Path(os.environ.get("MOVIEDB_API_USAGE_DATABASE") or paths.DATA_DIR / "api-usage.db")


def _connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS request_usage (account TEXT NOT NULL, sent_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS request_usage_account ON request_usage(account, sent_at);
        CREATE TABLE IF NOT EXISTS api_response (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL);
    """)
    return db


def reserve(account, limit, *, now=None):
    """A rolling 24-hour budget avoids guessing the provider's reset timezone.

    Reservations include failed requests/retries and are shared across processes.
    Never refund an attempt: a network error does not prove it wasn't counted.
    """
    now = time.time() if now is None else now
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM request_usage WHERE sent_at <= ?", (now - 86400,))
        count, oldest = db.execute("SELECT COUNT(*), MIN(sent_at) FROM request_usage WHERE account = ?", (account,)).fetchone()
        if count >= limit:
            from connectors import events
            delay = max(1, oldest + 86400 - now)
            events.emit("wait", service="mdblist", code="quota", reason="额度用尽", until=now + delay)
            raise RateLimitError("mdblist", delay, reason="quota")
        db.execute("INSERT INTO request_usage VALUES (?, ?)", (account, now))


def cached(key):
    with closing(_connect()) as db:
        row = db.execute("SELECT payload FROM api_response WHERE cache_key = ? AND expires_at > ?", (key, time.time())).fetchone()
    return json.loads(row[0]) if row else None


def remember(key, payload, ttl):
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM api_response WHERE expires_at <= ?", (time.time(),))
        db.execute("INSERT OR REPLACE INTO api_response VALUES (?, ?, ?)", (key, json.dumps(payload), time.time() + ttl))
