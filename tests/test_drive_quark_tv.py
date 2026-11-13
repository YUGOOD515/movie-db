import hashlib
from unittest.mock import Mock

import pytest

from connectors.drives import DRIVERS
from connectors.drives.base import AuthError, Entry
from connectors.drives.quark_tv import QuarkTVDrive, UCTVDrive


def response(payload, status=200):
    item = Mock(status_code=status, headers={})
    item.json.return_value = payload
    return item


def drive(session, saved=None, cls=QuarkTVDrive, **config):
    return cls({"refresh_token": "r1", "device_id": "dev", "root_id": "0", **config}, saved or Mock(), session)


def token_ok(access="a1", refresh="r2"):
    return response({"code": 200, "data": {"access_token": access, "refresh_token": refresh}})


def test_registered_and_signs_every_request():
    assert DRIVERS["quark_tv"] is QuarkTVDrive and DRIVERS["uc_tv"] is UCTVDrive
    session = Mock()
    session.post.return_value = token_ok()
    session.request.return_value = response({"status": 200, "errno": 0, "data": {}})
    saved = Mock()
    assert drive(session, saved).check() == "已连接"
    # 先用 refresh_token 换 access_token，新的 refresh_token 立即保存
    assert session.post.call_args.kwargs["json"]["refresh_token"] == "r1"
    assert saved.call_args.args[0]["refresh_token"] == "r2"
    method, url = session.request.call_args.args
    headers, params = session.request.call_args.kwargs["headers"], session.request.call_args.kwargs["params"]
    assert (method, url) == ("GET", "https://open-api-drive.quark.cn/user")
    expected = hashlib.sha256(f"GET&/user&{headers['x-pan-tm']}&{QuarkTVDrive.sign_key}".encode()).hexdigest()
    assert headers["x-pan-token"] == expected and headers["x-pan-client-id"] == QuarkTVDrive.client_id
    assert params["access_token"] == "a1" and params["device_id"] == "dev"
    assert params["req_id"] == hashlib.md5(f"dev{headers['x-pan-tm']}".encode()).hexdigest()


def test_invalid_token_refreshes_once_then_reports_relogin():
    session = Mock()
    session.post.side_effect = [token_ok("a1"), token_ok("a2", "r3")]
    session.request.side_effect = [
        response({"status": -1, "errno": 11001, "error_info": "access token invalid"}),
        response({"status": 200, "errno": 0, "data": {}}),
    ]
    driver = drive(session)
    assert driver.check() == "已连接" and driver.access_token == "a2"

    session.post.side_effect = [response({"code": 401, "message": "refresh token expired"})]
    session.request.side_effect = [response({"status": -1, "errno": 10001})]
    with pytest.raises(AuthError, match="重新扫码"):
        driver.check()


def test_requires_login_before_use():
    with pytest.raises(AuthError, match="扫码"):
        QuarkTVDrive({}, Mock(), Mock()).check()


def test_qr_login_flow_generates_device_and_saves_token():
    session = Mock()
    session.request.side_effect = [
        response({"status": 200, "qr_data": "QRBASE64", "query_token": "qt"}),
        response({"status": 200, "errno": 0}),               # 还没扫码
        response({"status": 200, "code": "authcode"}),       # 已确认
    ]
    session.post.return_value = token_ok("a1", "fresh")
    config = {}
    driver = QuarkTVDrive(config, config.update, session)
    login = driver.start_login()
    assert login == {"qr": "QRBASE64", "state": {"query_token": "qt"}} and len(driver.config["device_id"]) == 32
    assert driver.finish_login(login["state"]) is False
    assert driver.finish_login(login["state"]) is True
    assert session.post.call_args.kwargs["json"]["code"] == "authcode"
    assert config["refresh_token"] == "fresh" and config["device_id"] == driver.config["device_id"]


def test_list_pages_and_link_shape():
    session = Mock()
    session.post.return_value = token_ok()
    first = [{"fid": f"f{i}", "filename": f"{i}.mkv", "isdir": 0, "size": 9, "updated_at": 1000} for i in range(100)]
    session.request.side_effect = [
        response({"status": 200, "data": {"total_count": 101, "files": first}}),
        response({"status": 200, "data": {"total_count": 101, "files": [
            {"fid": "d", "filename": "Season 1", "isdir": 1, "updated_at": 1000}]}}),
        response({"status": 200, "data": {"download_url": "https://video.invalid/x"}}),
    ]
    driver = drive(session, cls=UCTVDrive)
    entries = driver.list(Entry("", True, "0"))
    assert len(entries) == 101 and entries[-1].is_dir and not entries[0].is_dir
    assert session.request.call_args_list[1].kwargs["params"]["page_index"] == "1"
    assert session.request.call_args_list[0].args[1].startswith("https://open-api-drive.uc.cn")
    link = driver.link(entries[0])
    assert link.url == "https://video.invalid/x" and link.proxy and "User-Agent" in link.headers
