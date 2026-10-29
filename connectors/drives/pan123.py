from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link

BASE = "https://open-api.123pan.com"
CN_TZ = timezone(timedelta(hours=8))


def _iso(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed).isoformat()


class Pan123Drive(Drive):
    kind = "pan123"
    label = "123 云盘 Open"
    fields = (
        Field("refresh_token", "refresh_token", "secret", hint="任选一种凭据"),
        Field("client_id", "client_id", "secret", hint="与 client_secret 一起填写"),
        Field("client_secret", "client_secret", "secret"),
        Field("access_token", "access_token", "secret", hint="约 90 天有效，到期需手动更新"),
        Field("renew_api", "续期服务", default="https://api.oplist.org/123cloud/renewapi", advanced=True),
        Field("root_id", "根目录 ID", default="0", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = str(config.get("root_id") or "0")
        if not any((config.get("refresh_token"), config.get("access_token"),
                    config.get("client_id") and config.get("client_secret"))):
            raise ValueError("请填写 refresh_token、client_id/client_secret 或 access_token 之一")
        self._token_lock = threading.Lock()

    def _expired(self):
        try:
            expires = datetime.fromisoformat(self.config.get("expires_at", "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return True
        return expires.timestamp() - time.time() < 300

    def _refresh(self):
        try:
            if self.config.get("refresh_token"):
                response = throttle.call("pan123", lambda: self.session.get(
                    self.config.get("renew_api") or "https://api.oplist.org/123cloud/renewapi",
                    params={"refresh_ui": self.config["refresh_token"], "server_use": "true",
                            "driver_txt": "123cloud_oa"}, timeout=20,
                ))
                payload = response.json()
                access = payload.get("access_token") or ""
                refresh = payload.get("refresh_token") or ""
                expires = datetime.now(timezone.utc) + timedelta(seconds=float(payload.get("expires_in") or 0))
                expires_at = expires.isoformat()
            else:
                response = throttle.call("pan123", lambda: self.session.post(
                    BASE + "/api/v1/access_token", headers={"Platform": "open_platform"},
                    json={"clientID": self.config.get("client_id", ""),
                          "clientSecret": self.config.get("client_secret", "")}, timeout=20,
                ))
                payload = response.json()
                data = payload.get("data") or {}
                access = data.get("accessToken") or ""
                refresh = ""
                expires_at = data.get("expiredAt") or ""
                if payload.get("code") != 0:
                    access = ""
        except (ValueError, TypeError, KeyError):
            access = refresh = expires_at = ""
        if not access or not expires_at:
            raise AuthError("123 云盘凭据已失效，请重新获取")
        self.config["access_token"] = access
        self.config["expires_at"] = expires_at
        if refresh:
            self.config["refresh_token"] = refresh
        self.save(self.config)

    def _token(self, force=False):
        with self._token_lock:
            renewable = bool(self.config.get("refresh_token") or
                             (self.config.get("client_id") and self.config.get("client_secret")))
            if not force and self.config.get("access_token") and (not renewable or not self._expired()):
                return self.config["access_token"]
            if not renewable:
                if self.config.get("access_token"):
                    return self.config["access_token"]
                raise AuthError("123 云盘 access_token 已失效，请更新")
            self._refresh()
            return self.config["access_token"]

    @staticmethod
    def _throttled(response):
        try:
            return response.json().get("code") == 429
        except ValueError:
            return False

    def _request(self, path, params, *, service="pan123"):
        for attempt in range(2):
            token = self._token(force=attempt == 1)
            response = throttle.call(service, lambda: self.session.get(
                BASE + path, params=params,
                headers={"Authorization": f"Bearer {token}", "Platform": "open_platform",
                         "Content-Type": "application/json"}, timeout=20,
            ), throttled=self._throttled)
            try:
                payload = response.json()
            except ValueError:
                raise DriveError("123 云盘返回了无法识别的响应") from None
            code = payload.get("code")
            if response.status_code == 401 or code == 401:
                if attempt == 0:
                    continue
                raise AuthError("123 云盘 access_token 已失效，请更新")
            if code != 0:
                raise DriveError("123 云盘请求失败，请检查配置")
            return payload.get("data") or {}
        raise DriveError("123 云盘请求失败，请检查配置")

    def check(self):
        data = self._request("/api/v1/user/info", {})
        return f"已连接：{data.get('nickname', '')}"

    def list(self, directory):
        result, last_id = [], 0
        while True:
            data = self._request("/api/v2/file/list", {
                "parentFileId": directory.id, "limit": 100, "lastFileId": last_id,
                "trashed": "false", "searchMode": "", "searchData": "",
            }, service="pan123-list")
            for item in data.get("fileList") or []:
                if item.get("trashed"):
                    continue
                result.append(Entry(
                    name=item.get("filename") or "", id=str(item.get("fileId") or ""),
                    is_dir=item.get("type") == 1, size=item.get("size") or 0,
                    modified=_iso(item.get("updateAt")), created=_iso(item.get("createAt")),
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            next_id = data.get("lastFileId")
            if next_id in (None, -1, "-1") or next_id == last_id:
                return result
            last_id = next_id

    def link(self, file):
        data = self._request("/api/v1/file/download_info", {"fileId": file.id}, service="pan123-link")
        if not data.get("downloadUrl"):
            raise DriveError("无法获取播放链接")
        return Link(url=data["downloadUrl"], proxy=True, concurrency=0, part_size=0, expires_in=None)
