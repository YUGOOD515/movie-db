import time
from unittest.mock import Mock

import pytest

from connectors.drives.alist import AListDrive
from connectors.drives.base import AuthError, Entry
from models import drive_store
from services import media_source


def response(status=200, payload=None):
    item = Mock(status_code=status)
    item.json.return_value = payload if payload is not None else {}
    item.close = Mock()
    return item


def drive(session):
    return AListDrive({"origin": "http://alist.invalid", "username": "user", "password": "secret"},
                      lambda config: None, session)


def test_login_is_cached_and_401_logs_in_again():
    session = Mock()
    unauthorized = response(401)
    unauthorized.json.side_effect = ValueError("not JSON")
    session.post.side_effect = [
        response(payload={"code": 200, "data": {"token": "old"}}),
        unauthorized,
        response(payload={"code": 200, "data": {"token": "new"}}),
        response(payload={"code": 200, "data": {"content": [{"name": "Movies", "is_dir": True}]}}),
    ]
    driver = drive(session)
    entries = driver.list(driver.root())
    assert entries[0].id == "/Movies"
    assert session.post.call_count == 4
    assert driver._login() == "new"
    assert session.post.call_count == 4


def test_link_with_and_without_sign():
    session = Mock()
    session.post.side_effect = [
        response(payload={"code": 200, "data": {"sign": "a b"}}),
        response(payload={"code": 200, "data": {"sign": ""}}),
    ]
    driver = drive(session)
    driver._token, driver._token_at = "token", time.monotonic()
    file = Entry("A.mkv", False, "/Movies/A.mkv")
    assert driver.link(file).url == "http://alist.invalid/d/Movies/A.mkv?sign=a+b"
    assert driver.link(file).url == "http://alist.invalid/d/Movies/A.mkv"


def test_invalid_credentials_do_not_leak_secrets():
    session = Mock()
    session.post.return_value = response(401, {"code": 401, "message": "bad secret"})
    driver = drive(session)
    with pytest.raises(AuthError) as caught:
        driver.check()
    assert "secret" not in str(caught.value) and "alist.invalid" not in str(caught.value)


def test_legacy_alist_configuration_migrates_once(monkeypatch):
    monkeypatch.setenv("ALIST_ORIGIN", "http://alist.invalid/")
    monkeypatch.setenv("ALIST_USERNAME", "user")
    monkeypatch.setenv("ALIST_PASSWORD", "secret")
    monkeypatch.setenv("ALIST_PATH_PASSWORD", "path-secret")
    assert media_source.migrate_alist() is True
    row = drive_store.get_drive("*")
    assert row["kind"] == "alist" and row["enabled"] is True
    assert row["config"] == {"origin": "http://alist.invalid/", "username": "user",
                             "password": "secret", "path_password": "path-secret"}
    assert media_source.migrate_alist() is False
