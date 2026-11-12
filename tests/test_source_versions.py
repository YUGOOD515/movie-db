"""同一文件经不同驱动读取时，时间写法不同也应视为同一版本；探测失败给出可操作的原因。"""
import sqlite3

from connectors import media_probe
from connectors.drives.base import Entry, same_instant, utc_iso
from models import drive_store, local_store
from services.import_jobs import same_version


def test_drive_times_are_normalized_to_utc():
    assert utc_iso("2026-10-27T18:23:33.992+08:00") == "2026-10-27T10:23:33.992+00:00"
    assert utc_iso("2026-10-27T10:23:33.992000+00:00") == "2026-10-27T10:23:33.992+00:00"
    assert utc_iso("2026-10-27T10:23:33Z") == "2026-10-27T10:23:33.000+00:00"
    assert Entry("a", False, "1", modified="2026-10-27T18:23:33.992+08:00").modified.endswith("+00:00")
    assert utc_iso("not a time") == "not a time"


def test_same_file_from_native_drive_and_alist_is_one_version():
    alist = [2624368088, "2026-10-27T18:23:33.992+08:00", "2026-10-27T18:23:33.992+08:00"]
    quark = [2624368088, "2026-10-27T10:23:33.992000+00:00", "2026-10-27T10:23:33.992000+00:00"]
    assert same_version(alist, quark)
    assert not same_version([1, *alist[1:]], quark)
    assert not same_instant("2026-10-27T10:23:33Z", "2026-10-27T10:25:33Z")


def test_probe_failures_explain_the_cause():
    message, code = media_probe.explain_failure(
        "Connection to tcp://127.0.0.1:5000 failed: Error number -138 occurred",
        "http://127.0.0.1:5000/stream/token.sig/a.mkv")
    assert code == "connection" and "127.0.0.1:5000" in message and "token" not in message
    assert media_probe.explain_failure("Server returned 403 Forbidden")[1] == "expired"
    assert media_probe.explain_failure("Server returned 404 Not Found")[1] == "missing"
    message, _ = media_probe.explain_failure("https://x.test/a?sig=secret: weird failure")
    assert "secret" not in message


def test_existing_disabled_mount_keeps_passthrough_after_upgrade(tmp_path, monkeypatch):
    """升级前“停用夸克 + 启用透传”实际由透传提供；首次建路由表时登记为移交，行为不变。"""
    database = tmp_path / "library.db"
    store = local_store.store_path(database)
    store.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(store) as db:
        db.executescript("""
            CREATE TABLE drive (mount TEXT NOT NULL PRIMARY KEY, kind TEXT NOT NULL, config TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
            INSERT INTO drive VALUES ('*', 'alist', '{}', 1, '', 0, 'x'), ('Quark', 'quark', '{}', 0, '', 0, 'x'),
                                     ('Local', 'local', '{}', 1, '', 0, 'x');
        """)
    monkeypatch.setattr(drive_store, "_routes_ready", set())
    assert drive_store.get_drive("Quark", database)["delegated"] is True
    assert drive_store.get_drive("Local", database)["delegated"] is False
    drive_store.set_delegated("Quark", False, database)
    monkeypatch.setattr(drive_store, "_routes_ready", set())
    assert drive_store.get_drive("Quark", database)["delegated"] is False  # 迁移只做一次


def test_drive_page_shows_route_and_handover_action(monkeypatch):
    from services import drives

    rows = {"*": {"mount": "*", "kind": "alist", "config": {}, "enabled": True, "status": "", "delegated": False},
            "Quark": {"mount": "Quark", "kind": "quark", "config": {}, "enabled": False, "status": "已连接", "delegated": True}}
    monkeypatch.setattr(drive_store, "list_drives", lambda: list(rows.values()))
    monkeypatch.setattr(drive_store, "get_drive", lambda mount: rows.get(mount))
    monkeypatch.setattr(drives, "_passthrough_names", lambda passthrough: frozenset({"Quark"}))
    views = {item["mount"]: item for item in drives.listing()}
    assert views["Quark"]["route_state"] == "delegated" and views["Quark"]["can_delegate"]
    assert views["Quark"]["status_text"] == "已移交透传" and "由默认（透传）提供" in views["Quark"]["scope"]
    assert "/Quark" in views["*"]["scope"]
    rows["Quark"].update(enabled=True, delegated=False)
    views = {item["mount"]: item for item in drives.listing()}
    assert views["Quark"]["route_state"] == "self" and "同名目录" in views["Quark"]["scope"]
