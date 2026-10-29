import os
from unittest.mock import Mock, patch

import pytest
import requests

with patch.dict(os.environ, {"MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.aliyun import AliyunDrive
from connectors.drives.baidu import BaiduDrive
from connectors.drives.base import DriveError, Entry, Link
from connectors.drives.pan123 import Pan123Drive
from connectors.drives.quark import QuarkDrive
from models import drive_store
from services import drives, media_source, setup_wizard
from test_media_stream import FakeResponse, FakeSession, install

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


def test_public_config_uses_whitelist_everywhere(env):
    drive_store.save_drive("aliyun", "aliyun", {
        "refresh_token": "refresh-secret", "access_token": "ali-access-secret",
        "drive_id": "derived-drive", "user_id": "derived-user", "drive_type": "resource", "root_id": "root",
    }, True)
    drive_store.save_drive("baidu", "baidu", {
        "refresh_token": "refresh-secret", "access_token": "baidu-access-secret", "root_path": "/",
    }, True)
    drive_store.save_drive("pan123", "pan123", {
        "access_token": "direct-secret", "expires_at": "derived-expiry", "root_id": "0",
    }, True)
    page = env["client"].get("/manager/drives", headers=HTML).get_data(as_text=True)
    status = env["client"].get("/setup/status").get_data(as_text=True)
    for output in (page, status):
        for secret in ("refresh-secret", "ali-access-secret", "baidu-access-secret", "direct-secret",
                       "derived-drive", "derived-user", "derived-expiry"):
            assert secret not in output
    for row in drives.listing():
        assert set(row["config"]) <= drives.PUBLIC_CONFIG_FIELDS[row["kind"]]


def test_drive_read_and_write_reject_tunnel(env):
    headers = {**ORIGIN, "Cf-Connecting-Ip": "1.2.3.4"}
    assert env["client"].get("/manager/drives", headers={**HTML, "Cf-Connecting-Ip": "1.2.3.4"}).status_code == 403
    assert env["client"].post("/manager/drives/save", json={}, headers=headers).status_code == 403
    assert env["client"].post("/manager/drives/local/test", headers=headers).status_code == 403
    assert env["client"].post("/manager/drives/local/toggle", headers=headers).status_code == 403
    assert env["client"].post("/manager/drives/local/delete", headers=headers).status_code == 403


def test_aliyun_check_always_refreshes_drive_info():
    session = Mock()
    session.post.return_value = Mock(status_code=200)
    session.post.return_value.json.return_value = {
        "resource_drive_id": "drive", "user_id": "user", "name": "Fresh name",
    }
    driver = AliyunDrive({"access_token": "access", "drive_id": "cached", "drive_type": "resource"}, Mock(), session)
    assert driver.check() == "已连接：Fresh name"
    assert session.post.call_count == 1
    assert session.post.call_args.args[0].endswith("/adrive/v1.0/user/getDriveInfo")


def test_edit_clears_derived_values_when_connection_inputs_change(env, monkeypatch):
    drive_store.save_drive("aliyun", "aliyun", {
        "refresh_token": "refresh", "access_token": "derived-access", "drive_id": "derived-drive",
        "user_id": "derived-user", "drive_type": "resource", "root_id": "root",
    }, True)
    seen = []

    class FakeDriver:
        def __init__(self, config, save):
            seen.append(dict(config))

        def check(self):
            return "已连接"

    monkeypatch.setattr(drives, "_driver", lambda kind, config, save: FakeDriver(config, save))
    result = drives.save("aliyun", "aliyun", {"drive_type": "default", "root_id": "new-root"})
    assert result["ok"]
    for key in ("access_token", "expires_at", "drive_id", "user_id"):
        assert key not in seen[0]


def test_edit_preserves_new_direct_access_token_but_clears_expiry(env, monkeypatch):
    drive_store.save_drive("pan123", "pan123", {
        "access_token": "old-access", "expires_at": "old-expiry", "root_id": "0",
    }, True)
    seen = []

    class FakeDriver:
        def __init__(self, config, save):
            seen.append(dict(config))

        def check(self):
            return "已连接"

    monkeypatch.setattr(drives, "_driver", lambda kind, config, save: FakeDriver(config, save))
    assert drives.save("pan123", "pan123", {"access_token": "new-access"})["ok"]
    assert seen[0]["access_token"] == "new-access" and "expires_at" not in seen[0]


@pytest.mark.parametrize("pages", [
    [{"code": 0, "data": {"fileList": []}}],
    [{"code": 0, "data": {"lastFileId": 1, "fileList": []}},
     {"code": 0, "data": {"lastFileId": 1, "fileList": []}}],
])
def test_pan123_stops_on_missing_or_repeated_cursor(pages):
    session = Mock()
    session.get.side_effect = [Mock(status_code=200, json=Mock(return_value=page)) for page in pages]
    driver = Pan123Drive({"access_token": "access"}, Mock(), session)
    assert driver.list(Entry("", True, "0")) == []


@pytest.mark.parametrize("error,status", [
    (FileNotFoundError("找不到：/quark/a.mkv"), 404),
    (DriveError("找不到挂载：quark"), 404),
    (DriveError("网盘读取失败"), 502),
    (requests.ConnectionError("offline"), 502),
])
def test_stream_maps_source_errors_to_chinese_responses(env, monkeypatch, error, status):
    monkeypatch.setattr(media_source, "parse_stream_token", lambda token: "/quark/a.mkv")
    monkeypatch.setattr(media_source, "open_link", Mock(side_effect=error))
    response = env["client"].get("/stream/good/a.mkv")
    assert response.status_code == status
    text = response.get_data(as_text=True)
    assert "找不到" in text if status == 404 else "网盘" in text
    assert "Traceback" not in text


def test_segmented_falls_back_to_single_connection_when_range_is_unsupported(env, monkeypatch):
    data = b"0123456789"
    session = FakeSession(handler=lambda url, kwargs: FakeResponse(200, data))
    install(monkeypatch, Link("https://offline.invalid/video", proxy=True, concurrency=1, part_size=4),
            session, Entry("a.mkv", False, "a", size=len(data)))
    monkeypatch.setattr(media_source, "parse_stream_token", lambda token: "/quark/a.mkv")
    response = env["client"].get("/stream/good/a.mkv")
    assert response.status_code == 200 and response.data == data
    assert len(session.calls) == 2


def test_segmented_rejects_wrong_content_range(env, monkeypatch):
    response_factory = lambda: FakeResponse(206, b"0123", {"Content-Range": "bytes 4-7/10"})
    session = FakeSession([response_factory(), response_factory()])
    install(monkeypatch, Link("https://offline.invalid/video", proxy=True, concurrency=1, part_size=4),
            session, Entry("a.mkv", False, "a", size=10))
    monkeypatch.setattr(media_source, "parse_stream_token", lambda token: "/quark/a.mkv")
    response = env["client"].get("/stream/good/a.mkv")
    assert response.data == b""
    assert len(session.calls) == 2


def test_baidu_fallback_dlink_contains_access_token():
    session = Mock()
    session.get.return_value = Mock(status_code=200, json=Mock(return_value={
        "errno": 0, "list": [{"dlink": "https://dlink.invalid/a?x=1"}],
    }))
    session.head.return_value = Mock(status_code=200, headers={})
    driver = BaiduDrive({"access_token": "access-secret"}, Mock(), session)
    link = driver.link(Entry("A.mkv", False, "/A.mkv", extra={"fs_id": 1}))
    assert "access_token=access-secret" in link.url


def test_star_mount_is_reserved_and_edit_can_rename(env):
    with pytest.raises(ValueError):
        drives.save("*", "local", {"root": str(env["tmp"])})
    assert drives.save("old", "local", {"root": str(env["tmp"])})["ok"]
    response = env["client"].post("/manager/drives/save", json={
        "mount": "new", "original_mount": "old", "kind": "local", "config": {"root": str(env["tmp"])},
    }, headers=ORIGIN)
    assert response.status_code == 200
    assert drive_store.get_drive("old") is None and drive_store.get_drive("new") is not None


def test_quark_accepts_numeric_status_as_text():
    session = Mock()
    session.request.return_value = Mock(status_code=200, headers={})
    session.request.return_value.json.return_value = {"code": 0, "status": "200", "data": {}}
    assert QuarkDrive({"cookie": "cookie"}, Mock(), session).check() == "已连接"


def test_setup_save_requires_an_enabled_drive(env):
    response = env["client"].post("/setup/save", json={"TMDB_TOKEN": "token"}, headers=ORIGIN)
    assert response.status_code == 400 and "至少" in response.get_json()["message"]
