import json
import os
import sqlite3
from contextlib import closing

from models import local_store


def _legacy_discovery(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE scan_root (path TEXT PRIMARY KEY, added_at REAL NOT NULL);
            CREATE TABLE import_job (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)
        db.execute("INSERT INTO scan_root VALUES (?, ?)", ("/share", 1))
        db.execute("INSERT INTO import_job VALUES (?, ?)", ("job-1", "{}"))
    with open(f"{path}-wal", "wb"):
        pass


def _legacy_api_usage(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE request_usage (account TEXT NOT NULL, sent_at REAL NOT NULL);
            CREATE TABLE api_response (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL);
        """)
        db.execute("INSERT INTO request_usage VALUES (?, ?)", ("account", 123))
        db.execute("INSERT INTO api_response VALUES (?, ?, ?)", ("key", json.dumps({"rating": 8}), 999999))


def test_upgrade_renames_discovery_and_merges_api_usage(monkeypatch, tmp_path):
    database = tmp_path / "library.db"
    monkeypatch.delenv("MOVIEDB_LOCAL_DATABASE", raising=False)
    monkeypatch.delenv("MOVIEDB_DISCOVERY_DATABASE", raising=False)
    discovery = tmp_path / "discovery.db"
    usage = tmp_path / "api-usage.db"
    _legacy_discovery(discovery)
    _legacy_api_usage(usage)
    with open(f"{usage}-wal", "wb"), open(f"{usage}-shm", "wb"):
        pass

    local_store.upgrade(database)

    local = tmp_path / "local.db"
    assert local.is_file()
    assert not discovery.exists() and not os.path.exists(f"{discovery}-wal")
    assert not usage.exists() and not os.path.exists(f"{usage}-wal") and not os.path.exists(f"{usage}-shm")
    assert local_store.list_roots(database) == ["/share"]
    with closing(local_store._connect(database)) as db:
        assert db.execute("SELECT id FROM import_job").fetchone()[0] == "job-1"
        assert db.execute("SELECT account, sent_at FROM request_usage").fetchone()[:] == ("account", 123)
        assert json.loads(db.execute("SELECT payload FROM api_response").fetchone()[0]) == {"rating": 8}

    local_store.add_root(database, "/other")
    local_store.upgrade(database)
    assert local_store.list_roots(database) == ["/share", "/other"]
    with closing(local_store._connect(database)) as db:
        assert db.execute("SELECT COUNT(*) FROM request_usage").fetchone()[0] == 1


def test_store_path_prefers_new_name_and_accepts_legacy_name(monkeypatch, tmp_path):
    database = tmp_path / "library.db"
    monkeypatch.delenv("MOVIEDB_LOCAL_DATABASE", raising=False)
    monkeypatch.setenv("MOVIEDB_DISCOVERY_DATABASE", str(tmp_path / "legacy-custom.db"))
    assert local_store.store_path(database) == tmp_path / "legacy-custom.db"
    monkeypatch.setenv("MOVIEDB_LOCAL_DATABASE", str(tmp_path / "new-custom.db"))
    assert local_store.store_path(database) == tmp_path / "new-custom.db"
