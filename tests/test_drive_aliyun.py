from unittest.mock import Mock

import pytest

from connectors.drives import DRIVERS
from connectors.drives.aliyun import AliyunDrive
from connectors.drives.base import AuthError, Entry


def response(payload, status=200, headers=None):
    item = Mock(status_code=status, headers=headers or {})
    item.json.return_value = payload
    item.close = Mock()
    return item


def config(**values):
    return {"refresh_token": "refresh-secret", "access_token": "access-secret", "drive_type": "resource",
            "drive_id": "drive", "root_id": "root", **values}


def test_check_initializes_and_persists_drive_info():
    session = Mock()
    session.post.return_value = response({"resource_drive_id": "drive", "user_id": "user", "name": "Tester"})
    saved = Mock()
    driver = AliyunDrive(config(drive_id=""), saved, session)
    assert driver.check() == "已连接：Tester"
    assert saved.call_args.args[0]["drive_id"] == "drive"
    assert saved.call_args.args[0]["user_id"] == "user"
    assert DRIVERS["aliyun"] is AliyunDrive


def test_list_paginates_and_link_has_one_minute_expiry():
    session = Mock()
    session.post.side_effect = [
        response({"items": [{"file_id": 1, "name": "Folder", "type": "folder", "size": 0,
                             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}],
                  "next_marker": "next"}),
        response({"items": [{"file_id": 2, "name": "A.mkv", "type": "file", "size": 42,
                             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}],
                  "next_marker": ""}),
        response({"url": "https://video.invalid/a"}),
    ]
    driver = AliyunDrive(config(), Mock(), session)
    entries = driver.list(Entry("", True, "root"))
    assert [(entry.id, entry.is_dir) for entry in entries] == [("1", True), ("2", False)]
    assert session.post.call_args_list[1].kwargs["json"]["marker"] == "next"
    link = driver.link(Entry("A.mkv", False, "2"))
    assert link.url == "https://video.invalid/a" and not link.proxy and link.expires_in == 60
    assert session.post.call_args_list[-1].kwargs["json"]["expire_sec"] == 14400


def test_expired_access_token_refreshes_and_persists_both_tokens():
    session = Mock()
    session.post.side_effect = [
        response({"code": "AccessTokenExpired", "message": "expired"}, 400),
        response({"resource_drive_id": "drive", "user_id": "user", "name": "Tester"}),
    ]
    session.get.return_value = response({"refresh_token": "new-refresh", "access_token": "new-access"})
    saved = Mock()
    driver = AliyunDrive(config(drive_id=""), saved, session)
    assert driver.check() == "已连接：Tester"
    assert saved.call_args_list[0].args[0]["refresh_token"] == "new-refresh"
    assert saved.call_args_list[0].args[0]["access_token"] == "new-access"
    assert session.get.call_args.kwargs["params"]["refresh_ui"] == "refresh-secret"
    assert session.post.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new-access"


def test_custom_client_credentials_use_oauth_endpoint():
    session = Mock()
    session.post.side_effect = [
        response({"refresh_token": "new-refresh", "access_token": "new-access"}),
        response({"default_drive_id": "default", "user_id": "user", "name": "Tester"}),
    ]
    driver = AliyunDrive(config(access_token="", drive_id="", drive_type="default",
                                client_id="client", client_secret="secret"), Mock(), session)
    assert driver.check() == "已连接：Tester"
    assert session.post.call_args_list[0].args[0].endswith("/oauth/access_token")
    assert session.post.call_args_list[0].kwargs["json"]["grant_type"] == "refresh_token"


def test_http_429_is_retried():
    session = Mock()
    session.post.side_effect = [
        response({"code": "TooManyRequests"}, 429, {"Retry-After": "0"}),
        response({"resource_drive_id": "drive", "user_id": "user", "name": "Tester"}),
    ]
    assert AliyunDrive(config(drive_id=""), Mock(), session).check() == "已连接：Tester"
    assert session.post.call_count == 2


def test_refresh_failure_is_auth_error_without_secrets():
    session = Mock()
    session.get.return_value = response({"text": "invalid refresh_secret"})
    with pytest.raises(AuthError) as caught:
        AliyunDrive(config(access_token="", drive_id=""), Mock(), session).check()
    assert "refresh_secret" not in str(caught.value) and "http" not in str(caught.value)
