from unittest.mock import Mock

import pytest

from connectors.drives import DRIVERS
from connectors.drives.base import AuthError, Entry
from connectors.drives.quark import QuarkDrive, UCDrive


def response(payload, status=200, headers=None):
    item = Mock(status_code=status, headers=headers or {})
    item.json.return_value = payload
    item.close = Mock()
    return item


def drive(session, saved=None, cls=QuarkDrive):
    return cls({"cookie": "a=1; __puus=old", "root_id": "0"}, saved or Mock(), session)


def test_registry_and_check():
    session = Mock()
    session.request.return_value = response({"code": 0, "status": 200, "data": {}})
    driver = drive(session)
    assert driver.check() == "已连接"
    assert DRIVERS["quark"] is QuarkDrive and DRIVERS["uc"] is UCDrive
    args, kwargs = session.request.call_args
    assert args == ("GET", "https://drive.quark.cn/1/clouddrive/config")
    assert kwargs["params"] == {"pr": "ucpro", "fr": "pc"}
    assert kwargs["headers"]["Referer"] == "https://pan.quark.cn"


def test_pagination_mapping_and_puus_persistence():
    first = response({"code": 0, "status": 200, "data": {"list": [{
        "fid": 123, "file_name": "A &amp; B.mkv", "file": True, "size": 42,
        "updated_at": 1000, "created_at": 2000,
    }]}, "metadata": {"_total": 101}}, headers={"Set-Cookie": "__puus=new; Path=/; HttpOnly"})
    second = response({"code": 0, "status": 200, "data": {"list": [{
        "fid": "dir", "file_name": "Folder", "file": False, "updated_at": 1000, "created_at": 2000,
    }]}, "metadata": {"_total": 101}})
    session = Mock()
    session.request.side_effect = [first, second]
    saved = Mock()
    entries = drive(session, saved).list(Entry("", True, "0"))
    assert [entry.name for entry in entries] == ["A & B.mkv", "Folder"]
    assert entries[0].id == "123" and not entries[0].is_dir and entries[0].size == 42
    assert entries[0].modified.endswith("+00:00") and entries[1].is_dir
    assert saved.call_args.args[0]["cookie"] == "a=1; __puus=new"
    assert session.request.call_args_list[1].kwargs["params"]["_page"] == 2


def test_link_shape_and_uc_constants():
    session = Mock()
    session.request.return_value = response({"code": 0, "status": 200, "data": [{"download_url": "https://video.invalid/a"}]})
    link = drive(session, cls=UCDrive).link(Entry("A", False, "fid"))
    assert link.url == "https://video.invalid/a" and link.proxy
    assert link.concurrency == 3 and link.part_size == 10 * 1024 * 1024 and link.expires_in is None
    assert link.headers["Referer"] == "https://drive.uc.cn"
    assert "uc-cloud-drive" in link.headers["User-Agent"]
    assert session.request.call_args.kwargs["json"] == {"fids": ["fid"]}


def test_throttled_message_is_retried():
    session = Mock()
    session.request.side_effect = [
        response({"code": 500, "status": 200, "message": "too many requests"}),
        response({"code": 0, "status": 200, "data": {}}),
    ]
    assert drive(session).check() == "已连接"
    assert session.request.call_count == 2


@pytest.mark.parametrize("payload,status", [
    ({"code": 401, "status": 401, "message": "login expired"}, 200),
    ({"code": 403, "status": 403, "message": "token invalid"}, 200),
    ({"code": 401, "status": 401, "message": ""}, 401),
])
def test_auth_error_has_no_credentials(payload, status):
    session = Mock()
    session.request.return_value = response(payload, status)
    with pytest.raises(AuthError) as caught:
        drive(session).check()
    assert "a=1" not in str(caught.value) and "http" not in str(caught.value)
