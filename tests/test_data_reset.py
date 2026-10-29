"""设置页“数据”分区：彻底删除所选数据、清空云端快照、安全限制。"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from models import local_store as store
from services import cloud_sync, data_reset, media_discovery, settings, setup_wizard

SAME_ORIGIN = {"Origin": "http://localhost"}
HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in (*settings.FIELDS, "FLASK_SECRET_KEY", "MOVIEDB_LOCAL_DATABASE", "MOVIEDB_DISCOVERY_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("ALIST_USERNAME=admin\nTMDB_TOKEN=secret\nMOVIEDB_SYNC_TOKEN=sync\n", encoding="utf-8")
    for key, value in (("ALIST_USERNAME", "admin"), ("TMDB_TOKEN", "secret"), ("MOVIEDB_SYNC_TOKEN", "sync")):
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(setup_wizard, "ENV_PATH", env_path)
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    Path(f"{database}-wal").write_bytes(b"x")
    store.add_root(database, "/share")
    images, thumbnails = tmp_path / "images", tmp_path / "thumbnails"
    (images / "tt1").mkdir(parents=True)
    (images / "tt1" / "poster.jpg").write_bytes(b"x" * 2048)
    thumbnails.mkdir()
    (thumbnails / "tones.json").write_text("{}", encoding="utf-8")
    keys = ("DATABASE", "TESTING", "READ_ONLY", "IMAGE_ROOT", "THUMBNAIL_ROOT")
    previous = {key: movie_app.app.config.get(key) for key in keys}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False, IMAGE_ROOT=images, THUMBNAIL_ROOT=thumbnails)
    movie_app.app.config.pop("LIBRARY_READY", None)
    yield {"tmp": tmp_path, "database": database, "env": env_path, "images": images, "thumbnails": thumbnails,
           "client": movie_app.app.test_client()}
    movie_app.app.config.update(previous)
    movie_app.app.config.pop("LIBRARY_READY", None)
    media_discovery._scans.clear()


def _clear(client, parts=data_reset.PART_IDS, confirm="清除", headers=SAME_ORIGIN):
    return client.post("/settings/clear", json={"parts": list(parts), "confirm": confirm}, headers=headers)


def test_state_lists_parts_with_sizes(env):
    data = env["client"].get("/settings/state").get_json()["data"]
    parts = {part["id"]: part for part in data["parts"]}
    assert list(parts) == list(data_reset.PART_IDS)
    assert parts["images"]["size"] >= 2048 and parts["library"]["exists"]
    assert data["cloud"] is True and data["confirm"] == "清除"


def test_clear_everything_empties_cloud_then_removes_env(env, monkeypatch):
    published = []

    def publish(database):
        published.append((database, setup_wizard.library_ready(database), os.environ.get("MOVIEDB_SYNC_TOKEN")))
        assert env["env"].exists()  # 发布需要 .env 里的同步令牌，必须在删除 .env 之前
        assert not env["images"].exists()  # 图片先删，快照里不再带旧海报
        return True

    monkeypatch.setattr(cloud_sync, "run_publish", publish)
    response = _clear(env["client"])
    body = response.get_json()
    assert response.status_code == 200 and body["ok"] and body["next"] == "/setup" and body["cloud"] == "cleared"
    assert len(published) == 1 and published[0][1] and published[0][2] == "sync"
    for path in (env["database"], Path(f"{env['database']}-wal"), env["tmp"] / "discovery.db",
                 env["images"], env["thumbnails"], env["env"]):
        assert not path.exists()
    assert (env["tmp"] / "local.db").is_file()
    assert not os.environ.get("TMDB_TOKEN") and not os.environ.get("MOVIEDB_SYNC_TOKEN")
    assert env["client"].get("/", headers=HTML).headers["Location"].endswith("/setup")
    assert store.list_roots(env["database"]) == []


def test_partial_clear_keeps_other_data(env, monkeypatch):
    monkeypatch.setattr(cloud_sync, "run_publish", lambda database: pytest.fail("只清图片不应发布快照"))
    body = _clear(env["client"], parts=["images"]).get_json()
    assert body["ok"] and body["next"] is None and body["cloud"] is None
    assert not env["images"].exists() and env["database"].exists() and env["env"].exists()
    assert os.environ["TMDB_TOKEN"] == "secret"


def test_local_clear_preserves_drive_until_config_is_cleared(env):
    db = store._connect(env["database"])
    with db:
        db.execute("CREATE TABLE drive (mount TEXT PRIMARY KEY)")
        db.execute("INSERT INTO drive VALUES ('quark')")
        db.execute("INSERT INTO request_usage VALUES ('account', 123)")
    db.close()

    body = _clear(env["client"], parts=["discovery"]).get_json()
    assert body["ok"] and env["env"].exists()
    assert store.list_roots(env["database"]) == []
    db = store._connect(env["database"])
    assert db.execute("SELECT mount FROM drive").fetchone()[0] == "quark"
    assert db.execute("SELECT COUNT(*) FROM request_usage").fetchone()[0] == 0
    db.close()

    body = _clear(env["client"], parts=["config"]).get_json()
    assert body["ok"] and not env["env"].exists()
    assert store.list_roots(env["database"]) == []
    db = store._connect(env["database"])
    assert db.execute("SELECT COUNT(*) FROM drive").fetchone()[0] == 0
    db.close()


def test_clear_requires_confirmation_selection_and_local_origin(env):
    client = env["client"]
    assert _clear(client, confirm="删除").status_code == 400
    assert _clear(client, parts=[]).status_code == 409
    assert _clear(client, headers={"Origin": "http://evil.example"}).status_code == 403
    assert _clear(client, headers={**SAME_ORIGIN, "Cf-Connecting-Ip": "1.2.3.4"}).status_code == 403
    media_discovery._scans[str(env["database"])] = {"status": "recognizing"}
    assert "扫描" in _clear(client).get_json()["message"]
    assert env["database"].exists()


def test_locked_file_is_reported(env, monkeypatch):
    original = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == "library.db":
            raise PermissionError(13, "Permission denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cloud_sync, "is_enabled", lambda: False)
    monkeypatch.setattr(Path, "unlink", unlink)
    body = _clear(env["client"]).get_json()
    assert body["ok"] is False and body["failed"] == ["library.db"] and body["next"] is None
    assert "重启" in body["message"]
