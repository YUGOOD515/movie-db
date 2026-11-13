"""扫码 / 账号密码 / 网页授权登录：阿里、百度、115、123（账号密码）、OneDrive / Google 回填。"""

import base64
import hashlib
import json
import os
import zlib
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

with patch.dict(os.environ, {"MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.aliyun import AliyunDrive
from connectors.drives.base import AuthError, Entry
from connectors.drives.baidu import DEFAULT_CLIENT, BaiduDrive
from connectors.drives.gdrive import GoogleDriveDrive
from connectors.drives.onedrive import OneDriveDrive
from connectors.drives.pan115 import Pan115Drive
from connectors.drives.pan123_web import CST, SIGN_TABLE, Pan123WebDrive, sign_path
from desktop_window import is_authorize_url
from models import drive_store
from services import drives, setup_wizard


def response(payload, status=200, headers=None):
    item = Mock(status_code=status, headers=headers or {})
    item.json.return_value = payload
    return item


# ---- 阿里云盘：经 OpenList 生成官方二维码 ----

def test_aliyun_qr_login_via_oplist():
    session = Mock()
    session.get.side_effect = [
        response({"text": "https://openapi.alipan.com/oauth/qrcode/abc", "sid": "abc"}),
        response({"status": "WaitLogin"}),
        response({"status": "LoginSuccess", "authCode": "code"}),
        response({"access_token": "a1", "refresh_token": "r1"}),
    ]
    config = {}
    driver = AliyunDrive(config, config.update, session)
    login = driver.start_login()
    assert login == {"qr": "https://openapi.alipan.com/oauth/qrcode/abc", "state": {"sid": "abc"}}
    assert session.get.call_args_list[0].args[0] == "https://api.oplist.org/alicloud/requests"
    assert driver.finish_login(login["state"]) is False
    assert driver.finish_login(login["state"]) is True
    callback = session.get.call_args_list[3]
    assert callback.args[0] == "https://api.oplist.org/alicloud/callback" and callback.kwargs["params"]["code"] == "abc"
    assert config["refresh_token"] == "r1" and config["alipan_type"] == "default"


def test_aliyun_qr_expired():
    session = Mock()
    session.get.return_value = response({"status": "QRCodeExpired"})
    with pytest.raises(AuthError, match="过期"):
        AliyunDrive({}, Mock(), session).finish_login({"sid": "abc"})


# ---- 百度：官方设备码 ----

def test_baidu_device_code_login_saves_client_with_token():
    session = Mock()
    session.get.side_effect = [
        response({"device_code": "dc", "user_code": "U1", "verification_url": "https://openapi.baidu.com/device",
                  "qrcode_url": "https://openapi.baidu.com/device/qrcode/xyz", "expires_in": 300, "interval": 5}),
        response({"error": "authorization_pending"}),
        response({"access_token": "a1", "refresh_token": "r1"}),
    ]
    config = {}
    driver = BaiduDrive(config, config.update, session)
    login = driver.start_login()
    assert login["qr"].endswith("/device/qrcode/xyz") and "U1" in login["hint"]
    assert session.get.call_args_list[0].kwargs["params"]["client_id"] == DEFAULT_CLIENT[0]
    assert driver.finish_login(login["state"]) is False
    assert driver.finish_login(login["state"]) is True
    assert session.get.call_args.kwargs["params"]["grant_type"] == "device_token"
    # 续期要用签发令牌的同一个应用
    assert (config["client_id"], config["client_secret"]) == DEFAULT_CLIENT and config["refresh_token"] == "r1"


def test_baidu_slow_down_backs_off_and_poll_respects_interval(monkeypatch):
    session = Mock()
    session.get.return_value = response({"error": "slow_down"})
    state = {"device_code": "dc", "interval": 5}
    assert BaiduDrive({}, Mock(), session).finish_login(state) is False and state["interval"] == 10

    calls = []
    monkeypatch.setattr(BaiduDrive, "start_login", lambda self: {"qr": "https://q", "state": {"interval": 5}})
    monkeypatch.setattr(BaiduDrive, "finish_login", lambda self, login_state: calls.append(1) or False)
    clock = [100.0]
    monkeypatch.setattr(drives.time, "monotonic", lambda: clock[0])
    login_id = drives.qr_start("baidu", "百度", create=True)["login_id"]
    assert drives.qr_poll(login_id)["pending"] and len(calls) == 1
    clock[0] += 2   # 不到 5 秒：不去问百度
    assert drives.qr_poll(login_id)["pending"] and len(calls) == 1
    clock[0] += 4
    assert drives.qr_poll(login_id)["pending"] and len(calls) == 2


def test_manual_credentials_missing_message():
    with pytest.raises(ValueError, match="扫码或授权登录"):
        drives.save("百度", "baidu", {}, create=True)


def test_baidu_device_code_denied():
    session = Mock()
    session.get.return_value = response({"error": "access_denied", "error_description": "user denied"})
    with pytest.raises(AuthError, match="user denied"):
        BaiduDrive({}, Mock(), session).finish_login({"device_code": "dc"})


# ---- 115：官方 PKCE 设备码 ----

def test_pan115_pkce_qr_login():
    session = Mock()
    session.post.side_effect = [
        response({"state": 1, "code": 0, "data": {"uid": "u1", "time": 1700000000, "sign": "s", "qrcode": "x"}}),
        response({"state": 1, "code": 0, "data": {"access_token": "a1", "refresh_token": "r1"}}),
    ]
    session.get.side_effect = [response({"data": {"status": 1}}), response({"data": {"status": 2}})]
    config = {}
    driver = Pan115Drive(config, config.update, session)
    login = driver.start_login()
    sent = session.post.call_args_list[0].kwargs["data"]
    verifier = login["state"]["verifier"]
    assert sent["client_id"] == "100195125" and sent["code_challenge_method"] == "sha256"
    assert sent["code_challenge"] == base64.b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    assert login["qr"] == "https://qrcodeapi.115.com/api/1.0/web/1.0/qrcode?uid=u1"
    assert driver.finish_login(login["state"]) is False
    assert driver.finish_login(login["state"]) is True
    assert session.post.call_args.kwargs["data"] == {"uid": "u1", "code_verifier": verifier}
    assert config == {"refresh_token": "r1", "access_token": "a1"}


def test_pan115_qr_cancelled():
    session = Mock()
    session.get.return_value = response({"data": {"status": -2}})
    with pytest.raises(AuthError):
        Pan115Drive({}, Mock(), session).finish_login({"uid": "u", "time": "1", "sign": "s", "verifier": "v"})


# ---- 123 云盘（账号密码） ----

def test_pan123_sign_path_matches_openlist_algorithm():
    now = datetime(2026, 11, 13, 12, 34, tzinfo=CST)
    key, value = sign_path("/b/api/file/list/new", now=now, nonce="1234567")
    stamp = "".join(SIGN_TABLE[int(digit)] for digit in "202611131234")
    assert key == str(zlib.crc32(stamp.encode()))
    timestamp = str(int(now.timestamp()))
    data = "|".join([timestamp, "1234567", "/b/api/file/list/new", "web", "3", key])
    assert value == f"{timestamp}-1234567-{zlib.crc32(data.encode())}"


def test_pan123_web_login_relogin_list_and_link():
    session = Mock()
    session.post.side_effect = [
        response({"code": 200, "data": {"token": "t1"}}),
        response({"code": 200, "data": {"token": "t2"}}),
    ]
    session.request.side_effect = [
        response({"code": 401, "message": "token expired"}),
        response({"code": 0, "data": {"InfoList": [{"FileId": 1, "FileName": "电影", "Type": 1}], "Next": "1"}}),
        response({"code": 0, "data": {"InfoList": [{"FileId": 2, "FileName": "a.mkv", "Type": 0, "Size": 9,
                                                    "Etag": "e", "S3KeyFlag": "k"}], "Next": "-1"}}),
        response({"code": 0, "data": {"DownloadUrl": "https://web.invalid/dl?params="
                                      + base64.b64encode(b"https://real.invalid/file").decode()}}),
    ]
    session.get.return_value = response({}, 302, {"Location": "https://cdn.invalid/final"})
    config = {"username": "user@example.com", "password": "pw"}
    driver = Pan123WebDrive(config, Mock(), session)
    entries = driver.list(Entry("", True, "0"))
    assert session.post.call_args_list[0].kwargs["json"] == {"mail": "user@example.com", "password": "pw", "type": 2}
    assert driver.config["access_token"] == "t2"
    assert [(entry.name, entry.is_dir) for entry in entries] == [("电影", True), ("a.mkv", False)]
    params = session.request.call_args_list[1].kwargs["params"]
    assert params["parentFileId"] == "0" and len(params) == 14  # 13 个查询参数 + 签名
    link = driver.link(entries[1])
    assert session.request.call_args.kwargs["json"]["etag"] == "e"
    assert session.get.call_args.args[0] == "https://real.invalid/file"
    assert link.url == "https://cdn.invalid/final" and link.headers == {"Referer": "https://web.invalid/"}


def test_pan123_web_phone_login_failure():
    session = Mock()
    session.post.return_value = response({"code": 5113, "message": "账号或密码错误"})
    with pytest.raises(AuthError, match="账号或密码错误"):
        Pan123WebDrive({"username": "13800000000", "password": "x"}, Mock(), session).check()
    assert session.post.call_args.kwargs["json"] == {"passport": "13800000000", "password": "x", "remember": True}


# ---- OneDrive / Google：OpenList 授权页结果回填 ----

def fragment(data):
    return base64.b64encode(json.dumps(data).encode()).decode()


def test_oauth_urls_and_finish(monkeypatch):
    assert drives.oauth_url(OneDriveDrive) == "https://api.oplist.org/onedrive/requests?driver_txt=onedrive_pr&server_use=true"
    assert drives.oauth_url(GoogleDriveDrive).startswith("https://api.oplist.org/googleui/requests?driver_txt=googleui_go")
    assert drives.oauth_url(Pan115Drive) == ""
    monkeypatch.setattr(OneDriveDrive, "check", lambda self: "已连接")
    pasted = "https://api.oplist.org/#" + fragment({"access_token": "a", "refresh_token": "r1", "driver_txt": "onedrive_pr"})
    result = drives.oauth_finish("onedrive", "OD", pasted, create=True)
    assert result["ok"] and drive_store.get_drive("OD")["config"]["refresh_token"] == "r1"
    # 只粘贴 # 后面的部分也可以
    drives.oauth_finish("onedrive", "OD", fragment({"refresh_token": "r2"}))
    assert drive_store.get_drive("OD")["config"]["refresh_token"] == "r2"
    with pytest.raises(ValueError, match="授权失败"):
        drives.oauth_finish("onedrive", "OD", fragment({"message_err": "denied"}))
    with pytest.raises(ValueError, match="无法识别"):
        drives.oauth_finish("onedrive", "OD", "https://example.com/not-a-token")
    # 在浏览器里授权时，直接粘贴 OpenList 页面上显示的 Refresh Token
    drives.oauth_finish("onedrive", "OD", "M.C512_BAY.0.U.-Cg!refresh*token$value")
    assert drive_store.get_drive("OD")["config"]["refresh_token"] == "M.C512_BAY.0.U.-Cg!refresh*token$value"
    page = drives.oauth_page(GoogleDriveDrive)
    assert page.startswith("https://api.oplist.org/#")
    assert json.loads(base64.b64decode(page.split("#", 1)[1])) == {"driver_txt": "googleui_go", "server_use": "true"}
    with pytest.raises(ValueError, match="不支持网页授权"):
        drives.oauth_finish("pan115", "X", fragment({"refresh_token": "r"}))


def test_authorize_window_only_opens_oplist():
    assert is_authorize_url("https://api.oplist.org/onedrive/requests?x=1")
    assert is_authorize_url("https://api-cn.oplist.org.cn/googleui/requests")
    assert not is_authorize_url("http://api.oplist.org/onedrive/requests")
    assert not is_authorize_url("https://evil.example/oplist.org")
    assert not is_authorize_url("https://oplist.org.evil.example/")


def test_forms_hide_manual_credentials_and_show_login_buttons(tmp_path):
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    try:
        page = movie_app.app.test_client().get("/media?tab=drives", headers={"Accept": "text/html"}).get_data(as_text=True)
    finally:
        movie_app.app.config.update(previous)
    for kind in ("quark_tv", "uc_tv", "aliyun", "baidu", "pan115"):
        section = page.split(f'data-drive-fields="{kind}"', 1)[1].split("data-drive-fields=", 1)[0]
        assert "data-drive-qr-start" in section and "手动填写凭据" in section
    for kind, embedded in (("onedrive", "true"), ("gdrive", "false")):
        section = page.split(f'data-drive-fields="{kind}"', 1)[1].split("data-drive-fields=", 1)[0]
        assert f'data-oauth-embedded="{embedded}"' in section and "手动填写凭据" in section
        assert 'data-oauth-page="https://api.oplist.org/#' in section
    section = page.split('data-drive-fields="pan123_web"', 1)[1].split("data-drive-fields=", 1)[0]
    assert "账号" in section and "密码" in section and "data-drive-qr-start" not in section
