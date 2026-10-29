from unittest.mock import Mock

import pytest

from connectors.drives import DRIVERS
from connectors.drives.baidu import BaiduDrive
from connectors.drives.base import AuthError, Entry


def response(payload=None, status=200, headers=None):
    item = Mock(status_code=status, headers=headers or {})
    item.json.return_value = payload if payload is not None else {}
    item.close = Mock()
    return item


def config(**values):
    return {"refresh_token": "refresh-secret", "access_token": "access-secret", "root_path": "/", **values}


def test_check_and_registry():
    session = Mock()
    session.get.return_value = response({"errno": 0, "baidu_name": "Tester", "vip_type": 2})
    driver = BaiduDrive(config(), Mock(), session)
    assert driver.check() == "已连接：Tester（会员类型 2）"
    assert session.get.call_args.kwargs["params"] == {"method": "uinfo", "access_token": "access-secret"}
    assert DRIVERS["baidu"] is BaiduDrive


def test_list_paginates_and_maps_paths_and_fs_id():
    session = Mock()
    session.get.side_effect = [
        response({"errno": 0, "list": [{"server_filename": "A.mkv", "path": "/视频/A.mkv", "fs_id": 123,
                                       "isdir": 0, "size": 42, "server_mtime": 1000, "server_ctime": 900}]}),
        response({"errno": 0, "list": []}),
    ]
    entries = BaiduDrive(config(), Mock(), session).list(Entry("", True, "/"))
    assert len(entries) == 1 and entries[0].id == "/视频/A.mkv" and not entries[0].is_dir
    assert entries[0].extra["fs_id"] == 123 and entries[0].modified.endswith("+00:00")
    assert session.get.call_args_list[1].kwargs["params"]["start"] == 1000


def test_link_head_does_not_follow_redirect_and_uses_location():
    session = Mock()
    session.get.return_value = response({"errno": 0, "list": [{"dlink": "https://dlink.invalid/a"}]})
    session.head.return_value = response(status=302, headers={"Location": "https://video.invalid/a"})
    driver = BaiduDrive(config(), Mock(), session)
    link = driver.link(Entry("A.mkv", False, "/A.mkv", extra={"fs_id": 123}))
    assert link.url == "https://video.invalid/a" and link.proxy and link.expires_in == 3600
    assert link.concurrency == 0 and link.part_size == 0 and link.headers == {"User-Agent": "pan.baidu.com"}
    assert session.head.call_args.kwargs["allow_redirects"] is False
    assert session.head.call_args.kwargs["params"] == {"access_token": "access-secret"}


def test_token_error_refreshes_and_persists_tokens():
    session = Mock()
    session.get.side_effect = [
        response({"errno": -6}),
        response({"refresh_token": "new-refresh", "access_token": "new-access"}),
        response({"errno": 0, "baidu_name": "Tester", "vip_type": 1}),
    ]
    saved = Mock()
    assert BaiduDrive(config(), saved, session).check() == "已连接：Tester（会员类型 1）"
    assert saved.call_args.args[0]["refresh_token"] == "new-refresh"
    assert saved.call_args.args[0]["access_token"] == "new-access"
    assert session.get.call_args_list[1].kwargs["params"]["driver_txt"] == "baiduyun_go"
    assert session.get.call_args_list[2].kwargs["params"]["access_token"] == "new-access"


def test_errno_31034_is_retried():
    session = Mock()
    session.get.side_effect = [
        response({"errno": 31034}, headers={"Retry-After": "0"}),
        response({"errno": 0, "baidu_name": "Tester", "vip_type": 1}),
    ]
    assert BaiduDrive(config(), Mock(), session).check() == "已连接：Tester（会员类型 1）"
    assert session.get.call_count == 2


def test_refresh_failure_is_auth_error_without_secrets():
    session = Mock()
    session.get.return_value = response({"text": "invalid refresh_secret"})
    with pytest.raises(AuthError) as caught:
        BaiduDrive(config(access_token=""), Mock(), session).check()
    assert "refresh_secret" not in str(caught.value) and "http" not in str(caught.value)
