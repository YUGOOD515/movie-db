"""夸克 / UC 网盘电视版：扫码登录得到 refresh_token，比网页 Cookie 稳定，不需要手动复制。

参照 OpenList drivers/quark_uc_tv 改写，只保留列目录、取链接与登录。请求要带电视客户端身份与签名：
x-pan-token = sha256(方法&路径&毫秒时间戳&签名密钥)，req_id = md5(设备 ID + 时间戳)。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timezone

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link

UA = ("Mozilla/5.0 (Linux; U; Android 13; zh-cn; M2004J7AC Build/UKQ1.231108.001) "
      "AppleWebKit/533.1 (KHTML, like Gecko) Mobile Safari/533.1")
DEVICE = {
    "device_brand": "Xiaomi", "platform": "tv", "device_name": "M2004J7AC", "device_model": "M2004J7AC",
    "build_device": "M2004J7AC", "build_product": "M2004J7AC", "device_gpu": "Adreno (TM) 550", "activity_rect": "{}",
}
TOKEN_ERRNO = {10001, 11001}
logger = logging.getLogger(__name__)


def _md5(text):
    return hashlib.md5(text.encode()).hexdigest()


def _iso(milliseconds):
    return datetime.fromtimestamp((milliseconds or 0) / 1000, timezone.utc).isoformat()


class QuarkTVDrive(Drive):
    kind = "quark_tv"
    label = "夸克网盘"
    qr_app = "夸克 App"
    api = "https://open-api-drive.quark.cn"
    code_api = "https://api.extscreen.com/quarkdrive"
    client_id = "d3194e61504e493eb6222857bccfed94"
    sign_key = "kw2dvtd7p4t3pjl2d9ed9yc8yej8kw2d"
    app_ver = "1.8.2.2"
    channel = "GENERAL"
    fields = (
        Field("refresh_token", "refresh_token", "secret", manual=True, hint="扫码登录后自动保存，无需手动填写"),
        Field("device_id", "设备 ID", advanced=True, hint="扫码登录时自动生成，令牌与它绑定，请勿修改"),
        Field("root_id", "根目录 ID", default="0", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = str(config.get("root_id") or "0")
        self.access_token = ""
        self._refresh_lock = threading.Lock()

    # ---- 签名与请求 ----
    def _device_id(self):
        if not self.config.get("device_id"):
            self.config["device_id"] = _md5(str(time.time_ns()))
        return self.config["device_id"]

    def _sign(self, method, pathname):
        timestamp = str(int(time.time() * 1000))
        token = hashlib.sha256(f"{method}&{pathname}&{timestamp}&{self.sign_key}".encode()).hexdigest()
        return timestamp, token, _md5(self._device_id() + timestamp)

    def _common(self, req_id):
        return {"req_id": req_id, "app_ver": self.app_ver, "device_id": self._device_id(),
                "channel": self.channel, **DEVICE}

    def _call(self, method, pathname, params):
        timestamp, token, req_id = self._sign(method, pathname)
        query = {**self._common(req_id), "access_token": self.access_token, **params}
        headers = {"Accept": "application/json, text/plain, */*", "User-Agent": UA,
                   "x-pan-tm": timestamp, "x-pan-token": token, "x-pan-client-id": self.client_id}
        response = throttle.call("quark", lambda: self.session.request(
            method, self.api + pathname, params=query, headers=headers, timeout=20))
        try:
            return response.json()
        except ValueError:
            raise DriveError(f"{self.label}返回了无法识别的响应") from None

    @staticmethod
    def _token_invalid(payload):
        info = str(payload.get("error_info") or "").lower()
        return (payload.get("status") == -1 and payload.get("errno") in TOKEN_ERRNO) or any(
            word in info for word in ("access token", "access_token", "token无效", "token 无效"))

    def _request(self, method, pathname, params=None):
        for attempt in range(2):
            if not self.access_token:
                self._refresh()
            payload = self._call(method, pathname, params or {})
            if self._token_invalid(payload):
                if attempt == 0:
                    self._refresh(force=True)
                    continue
                logger.warning("%s token invalid after refresh: %s status=%r errno=%r error_info=%r", self.label,
                               pathname, payload.get("status"), payload.get("errno"), str(payload.get("error_info") or "")[:80])
                raise AuthError(f"{self.label}登录已失效，请重新扫码")
            if (payload.get("status") or 0) >= 400 or payload.get("errno"):
                raise DriveError(str(payload.get("error_info") or f"{self.label}请求失败"))
            return payload
        raise DriveError(f"{self.label}请求失败")

    def _exchange(self, *, refresh_token="", code=""):
        """用 refresh_token 或扫码得到的 code 换 access_token；新的 refresh_token 立即写回。"""
        _, _, req_id = self._sign("POST", "/token")
        body = {**self._common(req_id), **({"refresh_token": refresh_token} if refresh_token else {"code": code})}
        # 换令牌服务会用 403 拦下 python-requests 的默认 User-Agent，必须带上电视客户端的 UA
        response = throttle.call("quark", lambda: self.session.post(
            self.code_api + "/token", json=body, headers={"Accept": "application/json", "User-Agent": UA}, timeout=20))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        data = payload.get("data") or {}
        if payload.get("code") != 200 or not data.get("refresh_token"):
            # 记下服务端的说明（不含令牌），下次失效时能看出是哪一步、什么原因
            reason = str(payload.get("message") or payload.get("msg") or "").strip()[:80]
            logger.warning("%s token exchange failed (%s): HTTP %s, code=%r, message=%r", self.label,
                           "refresh" if refresh_token else "code", response.status_code, payload.get("code"), reason)
            raise AuthError(f"{self.label}登录已失效{f'（{reason}）' if reason else ''}，请重新扫码")
        self.access_token = data.get("access_token") or ""
        self.config["refresh_token"] = data["refresh_token"]
        self.save(self.config)

    def _refresh(self, force=False):
        with self._refresh_lock:
            if self.access_token and not force:
                return
            if not self.config.get("refresh_token"):
                raise AuthError(f"请先扫码登录{self.label}")
            self._exchange(refresh_token=self.config["refresh_token"])

    # ---- 扫码登录 ----
    def start_login(self):
        """返回 {"qr": base64 图片, "state": {"query_token": ...}}；同时确定设备 ID。"""
        payload = self._call("GET", "/oauth/authorize", {
            "auth_type": "code", "client_id": self.client_id, "scope": "netdisk",
            "qrcode": "1", "qr_width": "460", "qr_height": "460",
        })
        if not payload.get("qr_data") or not payload.get("query_token"):
            raise DriveError(str(payload.get("error_info") or "无法获取登录二维码，请稍后重试"))
        return {"qr": payload["qr_data"], "state": {"query_token": payload["query_token"]}}

    def finish_login(self, state):
        """扫码确认后返回 True 并保存 refresh_token；还没确认时返回 False。"""
        payload = self._call("GET", "/oauth/code", {
            "client_id": self.client_id, "scope": "netdisk", "query_token": state["query_token"],
        })
        code = payload.get("code") if isinstance(payload.get("code"), str) else ""
        if not code:
            return False
        self._exchange(code=code)
        return True

    # ---- 网盘操作 ----
    def check(self):
        self._request("GET", "/user", {"method": "user_info"})
        return "已连接"

    def list(self, directory):
        result, page = [], 0
        while True:
            payload = self._request("GET", "/file", {
                "method": "list", "parent_fid": directory.id, "order_by": "3", "desc": "1",
                "category": "", "source": "", "ex_source": "", "list_all": "0",
                "page_size": "100", "page_index": str(page),
            })
            data = payload.get("data") or {}
            files = data.get("files") or []
            for item in files:
                result.append(Entry(
                    name=item.get("filename") or "", id=str(item.get("fid") or ""),
                    is_dir=item.get("isdir") == 1, size=item.get("size") or 0,
                    modified=_iso(item.get("updated_at")), created=_iso(item.get("created_at")),
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            if not files or len(result) >= int(data.get("total_count") or 0):
                return result
            page += 1

    def link(self, file):
        payload = self._request("GET", "/file", {
            "method": "download", "group_by": "source", "fid": file.id,
            "resolution": "low,normal,high,super,2k,4k", "support": "dolby_vision",
        })
        url = (payload.get("data") or {}).get("download_url") or ""
        if not url:
            raise DriveError("无法获取播放链接")
        return Link(url=url, headers={"User-Agent": UA}, proxy=True, concurrency=3,
                    part_size=4 * 1024 * 1024, prefetch_size=128 * 1024 * 1024, expires_in=None)


class UCTVDrive(QuarkTVDrive):
    kind = "uc_tv"
    label = "UC 网盘"
    qr_app = "UC 浏览器 App"
    api = "https://open-api-drive.uc.cn"
    code_api = "https://api.extscreen.com/ucdrive"
    client_id = "5acf882d27b74502b7040b0c65519aa7"
    sign_key = "l3srvtd7p42l0d0x1u8d7yc8ye9kki4d"
    app_ver = "1.7.2.2"
    channel = "UCTVOFFICIALWEB"
