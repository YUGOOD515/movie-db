from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link

BASE = "https://pan.baidu.com/rest/2.0"
OAUTH = "https://openapi.baidu.com/oauth/2.0"
# 扫码登录（设备码模式）使用的应用凭据：AList 源码中公开的默认值；也可以在高级选项换成自己的应用
DEFAULT_CLIENT = ("hq9yQ9w9kR4YHj1kyYafLygVocobh7Sf", "YH2VpZcFJHYNnV6vLfHQXDBhcE7ZChyE")
TOKEN_ERRORS = {111, -6, 20016, 20017}


def _iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class BaiduDrive(Drive):
    kind = "baidu"
    label = "百度网盘"
    qr_app = "百度网盘 App"
    fields = (
        Field("refresh_token", "refresh_token", "secret", required=True, manual=True),
        Field("client_id", "client_id", "secret", advanced=True),
        Field("client_secret", "client_secret", "secret", advanced=True),
        Field("renew_api", "续期服务", default="https://api.oplist.org/baiduyun/renewapi", advanced=True),
        Field("root_path", "根目录路径", default="/", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = config.get("root_path") or "/"
        self._refresh_lock = threading.Lock()

    def _refresh(self):
        with self._refresh_lock:
            try:
                if self.config.get("client_id") and self.config.get("client_secret"):
                    response = throttle.call("baidu", lambda: self.session.get(
                        "https://openapi.baidu.com/oauth/2.0/token",
                        params={"grant_type": "refresh_token", "refresh_token": self.config.get("refresh_token", ""),
                                "client_id": self.config["client_id"], "client_secret": self.config["client_secret"]},
                        timeout=20,
                    ))
                else:
                    response = throttle.call("baidu", lambda: self.session.get(
                        self.config.get("renew_api") or "https://api.oplist.org/baiduyun/renewapi",
                        params={"refresh_ui": self.config.get("refresh_token", ""), "server_use": "true",
                                "driver_txt": "baiduyun_go"}, timeout=20,
                    ))
                payload = response.json()
                refresh = payload.get("refresh_token") or ""
                access = payload.get("access_token") or ""
            except (ValueError, TypeError, KeyError):
                refresh = access = ""
            if not refresh or not access:
                raise AuthError("百度网盘 refresh_token 已失效，请重新获取")
            self.config.update(refresh_token=refresh, access_token=access)
            self.save(self.config)

    # ---- 扫码登录：百度官方设备码模式，二维码图片由百度提供；令牌与应用绑定，续期也用同一组应用凭据 ----
    def _client(self):
        if self.config.get("client_id") and self.config.get("client_secret"):
            return self.config["client_id"], self.config["client_secret"]
        return DEFAULT_CLIENT

    def start_login(self):
        client_id, _ = self._client()
        response = throttle.call("baidu", lambda: self.session.get(OAUTH + "/device/code", params={
            "response_type": "device_code", "client_id": client_id, "scope": "basic,netdisk",
        }, headers={"User-Agent": "pan.baidu.com"}, timeout=20))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not payload.get("device_code") or not payload.get("qrcode_url"):
            raise DriveError(str(payload.get("error_description") or "无法获取登录二维码，请稍后重试"))
        where = payload.get("verification_url") or "https://openapi.baidu.com/device"
        hint = f"也可以在 {where} 输入 {payload.get('user_code', '')}"
        # 百度要求至少间隔 interval 秒才能查询一次，查得太快会一直返回 slow_down，永远换不到令牌
        return {"qr": payload["qrcode_url"], "hint": hint,
                "state": {"device_code": payload["device_code"], "interval": int(payload.get("interval") or 5)}}

    def finish_login(self, state):
        client_id, client_secret = self._client()
        response = throttle.call("baidu", lambda: self.session.get(OAUTH + "/token", params={
            "grant_type": "device_token", "code": state["device_code"],
            "client_id": client_id, "client_secret": client_secret,
        }, headers={"User-Agent": "pan.baidu.com"}, timeout=20))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if payload.get("error") == "slow_down":
            state["interval"] = int(state.get("interval") or 5) + 5
            return False
        if payload.get("error") == "authorization_pending":
            return False
        if not payload.get("refresh_token"):
            raise AuthError(str(payload.get("error_description") or "百度网盘登录失败，请重新扫码"))
        self.config.update(refresh_token=payload["refresh_token"], access_token=payload.get("access_token") or "",
                           client_id=client_id, client_secret=client_secret)
        self.save(self.config)
        return True

    @staticmethod
    def _throttled(response):
        try:
            return response.json().get("errno") == 31034
        except ValueError:
            return False

    def _request(self, path, params):
        for attempt in range(2):
            if not self.config.get("access_token"):
                self._refresh()
            response = throttle.call("baidu", lambda: self.session.get(
                BASE + path,
                params={**params, "access_token": self.config["access_token"]}, timeout=20,
            ), throttled=self._throttled)
            try:
                payload = response.json()
            except ValueError:
                raise DriveError("百度网盘返回了无法识别的响应") from None
            errno = payload.get("errno", 0)
            if errno in TOKEN_ERRORS:
                if attempt == 0:
                    self._refresh()
                    continue
                raise AuthError("百度网盘 access_token 已失效，请重新授权")
            if errno:
                raise DriveError("百度网盘请求失败，请检查配置")
            return payload
        raise DriveError("百度网盘请求失败，请检查配置")

    def check(self):
        payload = self._request("/xpan/nas", {"method": "uinfo"})
        return f"已连接：{payload.get('baidu_name', '')}（会员类型 {payload.get('vip_type', '')}）"

    def list(self, directory):
        result, start = [], 0
        while True:
            payload = self._request("/xpan/file", {
                "method": "list", "dir": directory.id, "web": "web", "order": "name",
                "start": start, "limit": 1000,
            })
            items = payload.get("list") or []
            for item in items:
                result.append(Entry(
                    name=item.get("server_filename") or "", id=item.get("path") or "",
                    is_dir=item.get("isdir") == 1, size=item.get("size") or 0,
                    modified=_iso(item.get("server_mtime") or 0), created=_iso(item.get("server_ctime") or 0),
                    extra={"fs_id": item.get("fs_id")},
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            if not items:
                return result
            start += 1000

    def link(self, file):
        payload = self._request("/xpan/multimedia", {
            "method": "filemetas", "fsids": json.dumps([file.extra["fs_id"]]), "dlink": 1,
        })
        items = payload.get("list") or []
        dlink = items[0].get("dlink", "") if items else ""
        if not dlink:
            raise DriveError("无法获取播放链接")
        response = throttle.call("baidu", lambda: self.session.head(
            dlink, params={"access_token": self.config["access_token"]},
            headers={"User-Agent": "pan.baidu.com"}, allow_redirects=False, timeout=20,
        ))
        url = response.headers.get("Location")
        if not url:
            from urllib.parse import urlencode
            url = dlink + ("&" if "?" in dlink else "?") + urlencode({"access_token": self.config["access_token"]})
        return Link(url=url, headers={"User-Agent": "pan.baidu.com"}, proxy=True,
                    concurrency=0, part_size=0, expires_in=3600)
