from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from connectors.drives import DRIVERS
from connectors.drives.base import AuthError, Entry
from connectors.drives.pan123 import Pan123Drive


def response(payload, status=200, headers=None):
    item = Mock(status_code=status, headers=headers or {})
    item.json.return_value = payload
    item.close = Mock()
    return item


def config(**values):
    return {"access_token": "access-secret", "root_id": "0", **values}


def test_direct_token_check_and_registry():
    session = Mock()
    session.get.return_value = response({"code": 0, "data": {"nickname": "Tester"}})
    driver = Pan123Drive(config(), Mock(), session)
    assert driver.check() == "已连接：Tester"
    assert session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer access-secret"
    assert session.get.call_args.kwargs["headers"]["Platform"] == "open_platform"
    assert DRIVERS["pan123"] is Pan123Drive


def test_list_paginates_filters_trashed_and_maps_time():
    session = Mock()
    session.get.side_effect = [
        response({"code": 0, "data": {"lastFileId": 10, "fileList": [
            {"fileId": 1, "filename": "Folder", "type": 1, "trashed": 0,
             "createAt": "2026-01-01 00:00:00", "updateAt": "2026-01-02 00:00:00"},
            {"fileId": 2, "filename": "Deleted", "type": 0, "trashed": 1},
        ]}}),
        response({"code": 0, "data": {"lastFileId": -1, "fileList": [
            {"fileId": 3, "filename": "A.mkv", "type": 0, "trashed": 0, "size": 42,
             "createAt": "2026-01-01 00:00:00", "updateAt": "2026-01-02 00:00:00"},
        ]}}),
    ]
    entries = Pan123Drive(config(), Mock(), session).list(Entry("", True, "0"))
    assert [(entry.id, entry.name) for entry in entries] == [("1", "Folder"), ("3", "A.mkv")]
    assert entries[0].is_dir and not entries[1].is_dir and entries[1].size == 42
    assert entries[0].modified.endswith("+08:00")
    assert session.get.call_args_list[1].kwargs["params"]["lastFileId"] == 10


def test_link_shape():
    session = Mock()
    session.get.return_value = response({"code": 0, "data": {"downloadUrl": "https://video.invalid/a"}})
    link = Pan123Drive(config(), Mock(), session).link(Entry("A.mkv", False, "3"))
    assert link.url == "https://video.invalid/a" and link.proxy
    assert link.concurrency == 0 and link.part_size == 0 and link.expires_in is None


def test_refresh_token_flow_persists_new_tokens():
    session = Mock()
    session.get.side_effect = [
        response({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}),
        response({"code": 0, "data": {"nickname": "Tester"}}),
    ]
    saved = Mock()
    driver = Pan123Drive(config(access_token="", refresh_token="refresh-secret"), saved, session)
    assert driver.check() == "已连接：Tester"
    updated = saved.call_args.args[0]
    assert updated["access_token"] == "new-access" and updated["refresh_token"] == "new-refresh"
    assert updated["expires_at"]
    assert session.get.call_args_list[0].kwargs["params"]["driver_txt"] == "123cloud_oa"
    assert session.get.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new-access"


def test_near_expiry_and_client_credentials_refresh():
    session = Mock()
    session.post.return_value = response({"code": 0, "data": {"accessToken": "new-access",
                                                               "expiredAt": "2027-01-01T00:00:00Z"}})
    session.get.return_value = response({"code": 0, "data": {"nickname": "Tester"}})
    saved = Mock()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    driver = Pan123Drive(config(access_token="old", expires_at=expires,
                                client_id="client", client_secret="secret"), saved, session)
    assert driver.check() == "已连接：Tester"
    assert session.post.call_args.kwargs["json"] == {"clientID": "client", "clientSecret": "secret"}
    assert saved.call_args.args[0]["access_token"] == "new-access"


def test_business_429_is_retried():
    session = Mock()
    session.get.side_effect = [
        response({"code": 429, "message": "busy"}, headers={"Retry-After": "0"}),
        response({"code": 0, "data": {"nickname": "Tester"}}),
    ]
    assert Pan123Drive(config(), Mock(), session).check() == "已连接：Tester"
    assert session.get.call_count == 2


def test_direct_token_401_is_auth_error_without_secrets():
    session = Mock()
    session.get.return_value = response({"code": 401, "message": "invalid access_secret"})
    with pytest.raises(AuthError) as caught:
        Pan123Drive(config(), Mock(), session).check()
    assert session.get.call_count == 2
    assert "access_secret" not in str(caught.value) and "http" not in str(caught.value)


def test_credentials_are_required():
    with pytest.raises(ValueError, match="之一"):
        Pan123Drive({}, Mock(), Mock())
