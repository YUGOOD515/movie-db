from __future__ import annotations

import threading
import time
from urllib.parse import quote, urlencode

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link, THROTTLED_MESSAGE

TIMEOUT = 20
TOKEN_TTL = 3600


class AListDrive(Drive):
    kind = "alist"
    label = "AList / OpenList（透传）"
    root_id = "/"
    fields = (
        Field("origin", "地址", default="http://127.0.0.1:5244"),
        Field("username", "用户名", required=True),
        Field("password", "密码", "secret", required=True),
        Field("path_password", "路径密码", "secret", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.origin = str(config.get("origin") or "http://127.0.0.1:5244").rstrip("/")
        self._token = ""
        self._token_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _throttled(response):
        if response.status_code != 200:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        return bool(THROTTLED_MESSAGE.search(str(payload.get("message") or "")))

    def _post(self, endpoint, body, token=""):
        headers = {"Authorization": token} if token else {}
        return throttle.call(
            "alist",
            lambda: self.session.post(self.origin + endpoint, headers=headers, json=body, timeout=TIMEOUT),
            throttled=self._throttled,
        )

    @staticmethod
    def _payload(response):
        try:
            payload = response.json()
        except ValueError:
            raise DriveError("AList 返回了无法识别的响应") from None
        if not isinstance(payload, dict):
            raise DriveError("AList 返回了无法识别的响应")
        return payload

    def _login(self, *, force=False):
        with self._lock:
            if not force and self._token and time.monotonic() - self._token_at < TOKEN_TTL:
                return self._token
            response = self._post("/api/auth/login", {
                "username": self.config.get("username", ""),
                "password": self.config.get("password", ""),
            })
            if response.status_code in (400, 401, 403):
                raise AuthError("AList 用户名或密码无效")
            if response.status_code >= 400:
                raise DriveError("AList 登录失败")
            payload = self._payload(response)
            token = (payload.get("data") or {}).get("token", "")
            if payload.get("code") not in (None, 200) or not token:
                if payload.get("code") in (400, 401, 403):
                    raise AuthError("AList 用户名或密码无效")
                raise DriveError("AList 登录失败")
            self._token = token
            self._token_at = time.monotonic()
            return token

    def _request(self, endpoint, body):
        for attempt in range(2):
            token = self._login(force=attempt == 1)
            response = self._post(endpoint, body, token)
            if response.status_code == 401:
                with self._lock:
                    self._token = ""
                if attempt == 0:
                    continue
                raise AuthError("AList 登录状态已失效")
            payload = self._payload(response)
            if payload.get("code") == 401:
                with self._lock:
                    self._token = ""
                if attempt == 0:
                    continue
                raise AuthError("AList 登录状态已失效")
            if response.status_code >= 400 or payload.get("code") not in (None, 200):
                raise DriveError("AList 请求失败，请检查挂载配置")
            return payload.get("data") or {}
        raise DriveError("AList 请求失败，请检查挂载配置")

    def check(self):
        self._login()
        self.list(self.root())
        return f"已连接：{self.origin}"

    def list(self, directory):
        data = self._request("/api/fs/list", {
            "path": directory.id,
            "password": self.config.get("path_password", ""),
            "page": 1,
            "per_page": -1,
            "refresh": False,
        })
        result = []
        for item in data.get("content") or []:
            path = directory.id.rstrip("/") + "/" + item["name"]
            result.append(Entry(
                name=item["name"], is_dir=bool(item.get("is_dir")), id=path,
                size=item.get("size") or 0, modified=item.get("modified") or "",
                created=item.get("created") or "",
            ))
        return result

    def link(self, file):
        data = self._request("/api/fs/get", {
            "path": file.id,
            "password": self.config.get("path_password", ""),
        })
        url = self.origin + "/d" + quote(file.id, safe="/")
        if data.get("sign"):
            url += "?" + urlencode({"sign": data["sign"]})
        return Link(url=url, proxy=False, expires_in=None)
