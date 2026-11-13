"""115 网盘开放平台：access_token 约 2 小时有效，refresh_token 只能用一次，换新后必须立即保存。

参照 OpenList drivers/115_open 与 OpenListTeam/115-sdk-go 改写。下载地址与请求时的 User-Agent 绑定，
播放时要带同一个 User-Agent。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
from datetime import datetime, timezone

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link

API = "https://proapi.115.com"
PASSPORT = "https://passportapi.115.com"
QRCODE = "https://qrcodeapi.115.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
PAGE = 1000


def _iso(seconds):
    try:
        return datetime.fromtimestamp(int(seconds or 0), timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


class Pan115Drive(Drive):
    kind = "pan115"
    label = "115 网盘"
    qr_app = "115 App"
    fields = (
        Field("refresh_token", "refresh_token", "secret", required=True, manual=True,
              hint="每次续期都会换新并自动保存"),
        Field("access_token", "access_token", "secret", manual=True, hint="可留空，会用 refresh_token 获取"),
        Field("app_id", "开放应用 AppID", default="100195125", advanced=True,
              hint="扫码登录使用；同一应用对同一账号最多同时登录 3 处"),
        Field("root_id", "根目录 ID", default="0", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = str(config.get("root_id") or "0")
        self._lock = threading.Lock()

    def _refresh(self, used):
        """used：发起续期时手里的 access_token；别的线程已经换过时直接复用，避免重复消耗一次性的 refresh_token。"""
        with self._lock:
            if self.config.get("access_token") and self.config.get("access_token") != used:
                return
            response = throttle.call("pan115", lambda: self.session.post(
                PASSPORT + "/open/refreshToken", data={"refresh_token": self.config.get("refresh_token", "")},
                headers={"User-Agent": UA}, timeout=20))
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            if payload.get("code") or payload.get("error") or not data.get("access_token"):
                raise AuthError("115 refresh_token 已失效，请重新获取")
            self.config["access_token"] = data["access_token"]
            if data.get("refresh_token"):
                self.config["refresh_token"] = data["refresh_token"]
            self.save(self.config)

    # ---- 扫码登录：115 开放平台官方 PKCE 设备码，令牌直接向 115 续期，不依赖第三方服务 ----
    def start_login(self):
        verifier = secrets.token_hex(32)
        challenge = base64.b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
        response = throttle.call("pan115", lambda: self.session.post(PASSPORT + "/open/authDeviceCode", data={
            "client_id": self.config.get("app_id") or "100195125",
            "code_challenge": challenge, "code_challenge_method": "sha256",
        }, headers={"User-Agent": UA}, timeout=20))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not data.get("uid"):
            raise DriveError(str(payload.get("message") or payload.get("error") or "无法获取登录二维码，请稍后重试"))
        return {"qr": f"{QRCODE}/api/1.0/web/1.0/qrcode?uid={data['uid']}",
                "state": {"uid": data["uid"], "time": str(data.get("time") or ""), "sign": data.get("sign") or "",
                          "verifier": verifier}}

    def finish_login(self, state):
        response = throttle.call("pan115", lambda: self.session.get(QRCODE + "/get/status/", params={
            "uid": state["uid"], "time": state["time"], "sign": state["sign"],
        }, headers={"User-Agent": UA}, timeout=30))
        try:
            status = int(((response.json().get("data") or {}).get("status")) or 0)
        except (ValueError, TypeError, AttributeError):
            status = 0
        if status in (-1, -2):
            raise AuthError("二维码已过期或已取消，请重新获取")
        if status != 2:
            return False
        response = throttle.call("pan115", lambda: self.session.post(PASSPORT + "/open/deviceCodeToToken", data={
            "uid": state["uid"], "code_verifier": state["verifier"],
        }, headers={"User-Agent": UA}, timeout=20))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not data.get("refresh_token"):
            raise AuthError(str(payload.get("message") or "115 登录失败，请重新扫码"))
        self.config.update(refresh_token=data["refresh_token"], access_token=data.get("access_token") or "")
        self.save(self.config)
        return True

    def _request(self, method, path, *, params=None, data=None):
        for attempt in range(2):
            token = self.config.get("access_token") or ""
            if not token:
                self._refresh(token)
                token = self.config["access_token"]
            response = throttle.call("pan115", lambda: self.session.request(
                method, API + path, params=params, data=data, timeout=20,
                headers={"Authorization": f"Bearer {token}", "User-Agent": UA}))
            try:
                payload = response.json()
            except ValueError:
                raise DriveError("115 返回了无法识别的响应") from None
            if payload.get("state"):
                return payload
            code = str(payload.get("code") or "")
            if code == "99" or code.startswith("401"):
                if attempt == 0:
                    self._refresh(token)
                    continue
                raise AuthError("115 授权已失效，请重新获取 refresh_token")
            raise DriveError(str(payload.get("message") or "115 请求失败，请检查配置"))
        raise DriveError("115 请求失败，请检查配置")

    def check(self):
        data = self._request("GET", "/open/user/info").get("data") or {}
        name = data.get("user_name") or ""
        return f"已连接：{name}" if name else "已连接"

    def list(self, directory):
        result, offset = [], 0
        while True:
            payload = self._request("GET", "/open/ufile/files", params={
                "cid": directory.id, "limit": PAGE, "offset": offset, "show_dir": 1, "cur": 1,
                "asc": 1, "o": "file_name",
            })
            items = payload.get("data") or []
            for item in items:
                result.append(Entry(
                    name=item.get("fn") or "", id=str(item.get("fid") or ""), is_dir=str(item.get("fc")) == "0",
                    size=int(item.get("fs") or 0), modified=_iso(item.get("upt")), created=_iso(item.get("uppt")),
                    extra={"pick_code": item.get("pc") or ""},
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            offset += PAGE
            if not items or offset >= int(payload.get("count") or 0):
                return result

    def link(self, file):
        pick_code = file.extra.get("pick_code") or ""
        if not pick_code:
            raise DriveError("无法获取播放链接")
        data = self._request("POST", "/open/ufile/downurl", data={"pick_code": pick_code}).get("data") or {}
        item = (data.get(file.id) or next(iter(data.values()), {})) if isinstance(data, dict) else {}
        url = ((item or {}).get("url") or {}).get("url") or ""
        if not url:
            raise DriveError("无法获取播放链接")
        return Link(url=url, headers={"User-Agent": UA}, proxy=True, concurrency=0, part_size=0, expires_in=1800)
