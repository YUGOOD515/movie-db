"""123 云盘（账号密码）：用手机号 / 邮箱和密码登录网页版接口，不需要开放平台的开发者凭据。

参照 OpenList drivers/123 改写，只保留登录、列目录与取链接。每个请求的网址都要带签名参数
（签名算法与 OpenList 的 signPath 一致）；令牌失效（code 401）时自动重新登录一次。
"""

from __future__ import annotations

import base64
import random
import threading
import zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link

API = "https://yun.123pan.com/b/api"
LOGIN = "https://login.123pan.com/api/user/sign_in"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
SIGN_TABLE = "adefghlmyijnopkqrstubcvwsz"
CST = timezone(timedelta(hours=8))


def sign_path(path, now=None, nonce=None):
    """返回签名参数 (键, 值)：键是时间签名，值是“时间戳-随机数-数据签名”。"""
    now = now or datetime.now(CST)
    nonce = str(round(1e7 * random.random())) if nonce is None else nonce
    timestamp = str(int(now.timestamp()))
    stamp = "".join(SIGN_TABLE[int(digit)] for digit in now.astimezone(CST).strftime("%Y%m%d%H%M"))
    time_sign = str(zlib.crc32(stamp.encode()))
    data_sign = zlib.crc32("|".join([timestamp, nonce, path, "web", "3", time_sign]).encode())
    return time_sign, f"{timestamp}-{nonce}-{data_sign}"


class Pan123WebDrive(Drive):
    kind = "pan123_web"
    label = "123 云盘"
    fields = (
        Field("username", "账号", required=True, hint="手机号或邮箱"),
        Field("password", "密码", "secret", required=True),
        Field("root_id", "根目录 ID", default="0", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = str(config.get("root_id") or "0")
        self._lock = threading.Lock()

    def _headers(self, token=""):
        headers = {"origin": "https://yun.123pan.com", "referer": "https://yun.123pan.com/",
                   "user-agent": UA, "platform": "web", "app-version": "3"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        return headers

    def _login(self, used=""):
        with self._lock:
            if self.config.get("access_token") and self.config.get("access_token") != used:
                return
            username = str(self.config.get("username") or "").strip()
            body = ({"mail": username, "password": self.config.get("password", ""), "type": 2} if "@" in username
                    else {"passport": username, "password": self.config.get("password", ""), "remember": True})
            response = throttle.call("pan123", lambda: self.session.post(
                LOGIN, json=body, headers=self._headers(), timeout=20))
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            token = (payload.get("data") or {}).get("token") if isinstance(payload.get("data"), dict) else ""
            if payload.get("code") != 200 or not token:
                raise AuthError(f"123 云盘登录失败：{payload.get('message') or '请检查账号和密码'}")
            self.config["access_token"] = token
            self.save(self.config)

    def _request(self, method, path, *, params=None, body=None, service="pan123-list"):
        for attempt in range(2):
            token = self.config.get("access_token") or ""
            if not token:
                self._login(token)
                token = self.config["access_token"]
            key, value = sign_path("/b/api" + path)
            query = {**(params or {}), key: value}
            response = throttle.call(service, lambda: self.session.request(
                method, API + path, params=query, json=body, headers=self._headers(token), timeout=20))
            try:
                payload = response.json()
            except ValueError:
                raise DriveError("123 云盘返回了无法识别的响应") from None
            code = payload.get("code")
            if code == 0:
                return payload.get("data") or {}
            if code == 401 and attempt == 0:
                self._login(token)
                continue
            if code == 401:
                raise AuthError("123 云盘登录已失效，请检查账号和密码")
            raise DriveError(str(payload.get("message") or "123 云盘请求失败"))
        raise DriveError("123 云盘请求失败")

    def check(self):
        data = self._request("GET", "/user/info")
        name = data.get("Nickname") or data.get("Passport") or ""
        return f"已连接：{name}" if name else "已连接"

    def list(self, directory):
        result, page = [], 1
        while True:
            data = self._request("GET", "/file/list/new", params={
                "driveId": "0", "limit": "100", "next": "0", "orderBy": "file_id", "orderDirection": "desc",
                "parentFileId": directory.id, "trashed": "false", "SearchData": "", "Page": str(page),
                "OnlyLookAbnormalFile": "0", "event": "homeListFile", "operateType": "4", "inDirectSpace": "false",
            })
            items = data.get("InfoList") or []
            for item in items:
                result.append(Entry(
                    name=item.get("FileName") or "", id=str(item.get("FileId") or ""), is_dir=item.get("Type") == 1,
                    size=item.get("Size") or 0, modified=item.get("UpdateAt") or "",
                    extra={key: item.get(key) for key in ("Etag", "S3KeyFlag", "Type", "Size", "FileName")},
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            if not items or str(data.get("Next")) == "-1":
                return result
            page += 1

    def link(self, file):
        extra = file.extra
        data = self._request("POST", "/file/download_info", body={
            "driveId": 0, "etag": extra.get("Etag") or "", "fileId": int(file.id),
            "fileName": extra.get("FileName") or file.name, "s3keyFlag": extra.get("S3KeyFlag") or "",
            "size": extra.get("Size") or file.size, "type": extra.get("Type") or 0,
        }, service="pan123-link")
        url = data.get("DownloadUrl") or ""
        if not url:
            raise DriveError("无法获取播放链接")
        parts = urlsplit(url)
        nested = parse_qs(parts.query).get("params", [""])[0]
        if nested:
            try:
                url = base64.b64decode(nested + "=" * (-len(nested) % 4)).decode()
            except (ValueError, UnicodeDecodeError):
                pass
        referer = {"Referer": f"{parts.scheme}://{parts.netloc}/"}
        response = throttle.call("pan123-link", lambda: self.session.get(
            url, headers={"Referer": "https://yun.123pan.com/"}, allow_redirects=False, timeout=20))
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
            url = response.headers["Location"]
        elif response.status_code < 300:
            try:
                url = ((response.json().get("data") or {}).get("redirect_url")) or url
            except ValueError:
                pass
        return Link(url=url, headers=referer, proxy=True, concurrency=0, part_size=0, expires_in=600)
