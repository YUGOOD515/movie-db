import os
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.base import DriveError
from models import drive_store
from services import drives, setup_wizard

ORIGIN = {"Origin": "http://localhost"}
HTML = {"Accept": "text/html"}


@pytest.fixture
def env(tmp_path):
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    yield {"client": movie_app.app.test_client(), "tmp": tmp_path}
    movie_app.app.config.update(previous)


def save(client, mount="local", kind="local", config=None):
    return client.post("/manager/drives/save", json={"mount": mount, "kind": kind, "config": config or {}},
                       headers=ORIGIN)


def test_local_drive_crud_page_and_json_are_safe(env):
    response = save(env["client"], config={"root": str(env["tmp"])})
    assert response.status_code == 200 and response.get_json()["ok"]
    assert "config" not in response.get_json() and str(env["tmp"]) not in response.get_data(as_text=True)
    assert drive_store.get_drive("local")["status"] == f"已连接：{env['tmp']}"

    page = env["client"].get("/manager/drives", headers=HTML)
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "本机目录" in text and "AList / OpenList（透传）" in text and "local" in text
    for label in ("夸克网盘", "UC 网盘", "阿里云盘 Open", "百度网盘", "123 云盘 Open"):
        assert label in text

    assert env["client"].post("/manager/drives/local/test", headers=ORIGIN).get_json()["ok"]
    toggled = env["client"].post("/manager/drives/local/toggle", headers=ORIGIN).get_json()
    assert toggled["enabled"] is False and drive_store.get_drive("local")["enabled"] is False
    assert env["client"].post("/manager/drives/local/delete", headers=ORIGIN).get_json()["ok"]
    assert drive_store.get_drive("local") is None


def test_secret_stays_on_edit_and_failed_check_does_not_save(env, monkeypatch):
    class FakeDriver:
        def __init__(self, config, save):
            self.config = config

        def check(self):
            if self.config.get("origin") == "bad":
                raise DriveError("检测失败")
            return "已连接"

    monkeypatch.setattr(drives, "_driver", lambda kind, config, save: FakeDriver(config, save))
    assert save(env["client"], "ignored", "alist", {"origin": "old", "username": "u", "password": "secret"}).status_code == 200
    row = drive_store.get_drive("*")
    assert row["config"]["password"] == "secret"
    assert "secret" not in str(drives.listing())

    assert save(env["client"], "ignored", "alist", {"origin": "new", "username": "u", "password": ""}).status_code == 200
    row = drive_store.get_drive("*")
    assert row["config"]["origin"] == "new" and row["config"]["password"] == "secret"

    response = save(env["client"], "other", "local", {"root": "bad"})
    assert response.status_code == 200  # 本机驱动不参与这次假驱动替换
    drive_store.delete_drive("other")
    response = save(env["client"], "ignored", "alist", {"origin": "bad", "username": "u", "password": "secret"})
    assert response.status_code == 400 and response.get_json()["ok"] is False
    assert drive_store.get_drive("*")["config"]["origin"] == "new"


def test_missing_mounts_use_first_path_segment_and_fallback():
    paths = ["/夸克/电影/a.mkv", "/夸克/电影/b.mkv", "/UC/a.mkv"]
    assert drives.missing_mounts(paths) == ["UC", "夸克"]
    drive_store.save_drive("夸克", "local", {"root": "/"}, True)
    assert drives.missing_mounts(paths) == ["UC"]
    drive_store.save_drive("*", "alist", {}, True)
    assert drives.missing_mounts(paths) == []
