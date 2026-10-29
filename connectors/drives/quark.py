from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link, THROTTLED_MESSAGE

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch"


def _iso(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


class QuarkDrive(Drive):
    kind = "quark"
    label = "夸克网盘"
    api = "https://drive.quark.cn/1/clouddrive"
    referer = "https://pan.quark.cn"
    pr = "ucpro"
    ua = UA
    fields = (
        Field("cookie", "Cookie", "secret", required=True, hint="浏览器登录网页版后复制完整 Cookie"),
        Field("root_id", "根目录 ID", default="0", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = str(config.get("root_id") or "0")
        self.cookie = config.get("cookie", "")

    def _save_cookie(self, response):
        value = response.headers.get("Set-Cookie", "")
        match = re.search(r"__puus=([^;,]+)", value)
        if not match:
            return
        replacement = f"__puus={match.group(1)}"
        parts = [part.strip() for part in self.cookie.split(";") if part.strip()]
        if any(part.startswith("__puus=") for part in parts):
            parts = [replacement if part.startswith("__puus=") else part for part in parts]
        else:
            parts.append(replacement)
        self.cookie = "; ".join(parts)
        self.config["cookie"] = self.cookie
        self.save(self.config)

    @staticmethod
    def _throttled(response):
        try:
            payload = response.json()
        except ValueError:
            return False
        return bool(THROTTLED_MESSAGE.search(str(payload.get("message") or "")))

    def _request(self, method, path, *, params=None, body=None, headers=None):
        query = {"pr": self.pr, "fr": "pc", **(params or {})}
        request_headers = {
            "Cookie": self.cookie,
            "Accept": "application/json, text/plain, */*",
            "Referer": self.referer,
            **(headers or {}),
        }
        response = throttle.call(
            "quark",
            lambda: self.session.request(method, self.api + path, params=query, json=body,
                                         headers=request_headers, timeout=20),
            throttled=self._throttled,
        )
        self._save_cookie(response)
        if response.status_code == 401:
            raise AuthError(f"{self.label} Cookie 已失效，请重新复制")
        try:
            payload = response.json()
        except ValueError:
            raise DriveError(f"{self.label}返回了无法识别的响应") from None
        message = str(payload.get("message") or "")
        if response.status_code >= 400 or payload.get("status", 0) >= 400 or payload.get("code") != 0:
            if re.search(r"登录|login|token", message, re.IGNORECASE):
                raise AuthError(f"{self.label} Cookie 已失效，请重新复制")
            raise DriveError(f"{self.label}请求失败，请检查配置")
        return payload

    def check(self):
        self._request("GET", "/config")
        return "已连接"

    def list(self, directory):
        result = []
        for page in range(1, 101):
            payload = self._request("GET", "/file/sort", params={
                "pdir_fid": directory.id, "_size": 100, "_page": page, "_fetch_total": 1,
                "fetch_all_file": 1, "fetch_risk_file_name": 1,
            })
            data = payload.get("data") or {}
            items = data.get("list") or []
            for item in items:
                result.append(Entry(
                    name=html.unescape(item.get("file_name") or ""), id=str(item.get("fid") or ""),
                    is_dir=not item.get("file"), size=item.get("size") or 0,
                    modified=_iso(item.get("updated_at") or 0), created=_iso(item.get("created_at") or 0),
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            total = (payload.get("metadata") or {}).get("_total", 0)
            if not items or page * 100 >= total:
                break
        return result

    def link(self, file):
        payload = self._request("POST", "/file/download", body={"fids": [file.id]},
                                headers={"User-Agent": self.ua})
        data = payload.get("data") or []
        url = data[0].get("download_url", "") if data else ""
        if not url:
            raise DriveError("无法获取播放链接")
        return Link(url=url, headers={"Cookie": self.cookie, "Referer": self.referer, "User-Agent": self.ua},
                    proxy=True, concurrency=3, part_size=10 * 1024 * 1024, expires_in=None)


class UCDrive(QuarkDrive):
    kind = "uc"
    label = "UC 网盘"
    api = "https://pc-api.uc.cn/1/clouddrive"
    referer = "https://drive.uc.cn"
    pr = "UCBrowser"
    ua = UA.replace("quark-cloud-drive", "uc-cloud-drive")
