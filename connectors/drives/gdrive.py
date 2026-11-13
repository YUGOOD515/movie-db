"""Google Drive（Drive API v3）：按文件 ID 列目录；读取文件要带 access_token，约 1 小时有效。

参照 OpenList drivers/google_drive 改写（不含服务账号）。默认通过 OpenList 令牌服务续期；
填了自己的 client_id / client_secret 时直接向 Google 续期。
"""

from __future__ import annotations

import threading

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link, renew_online

API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
FIELDS = "files(id,name,mimeType,size,modifiedTime,createdTime,shortcutDetails),nextPageToken"


class GoogleDriveDrive(Drive):
    kind = "gdrive"
    label = "Google Drive"
    oauth_txt = "googleui_go"
    oauth_embedded = False      # Google 拒绝在内嵌浏览器里登录，改用系统浏览器 + 粘贴网址
    fields = (
        Field("refresh_token", "refresh_token", "secret", required=True, manual=True),
        Field("client_id", "client_id", "secret", advanced=True, hint="留空则通过 OpenList 的令牌服务续期"),
        Field("client_secret", "client_secret", "secret", advanced=True, hint="留空则通过 OpenList 的令牌服务续期"),
        Field("renew_api", "续期服务", default="https://api.oplist.org/googleui/renewapi", advanced=True),
        Field("root_id", "根目录 ID", default="root", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = str(config.get("root_id") or "root")
        self._lock = threading.Lock()

    def _refresh(self):
        with self._lock:
            if self.config.get("client_id") and self.config.get("client_secret"):
                response = throttle.call("gdrive", lambda: self.session.post(
                    "https://www.googleapis.com/oauth2/v4/token", timeout=20, data={
                        "grant_type": "refresh_token", "refresh_token": self.config.get("refresh_token", ""),
                        "client_id": self.config["client_id"], "client_secret": self.config["client_secret"],
                    }))
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                access = payload.get("access_token") or ""
                refresh = payload.get("refresh_token") or self.config.get("refresh_token", "")
                if not access:
                    raise AuthError("Google Drive refresh_token 已失效，请重新获取")
            else:
                access, refresh = renew_online(
                    self.session, "gdrive", self.config.get("renew_api") or "https://api.oplist.org/googleui/renewapi",
                    self.config.get("refresh_token", ""), "googleui_go", "Google Drive")
            self.config.update(access_token=access, refresh_token=refresh)
            self.save(self.config)

    def _get(self, path, params=None):
        query = {"includeItemsFromAllDrives": "true", "supportsAllDrives": "true", **(params or {})}
        for attempt in range(2):
            if not self.config.get("access_token"):
                self._refresh()
            response = throttle.call("gdrive", lambda: self.session.get(
                API + path, params=query, headers={"Authorization": f"Bearer {self.config['access_token']}"},
                timeout=20))
            try:
                payload = response.json()
            except ValueError:
                raise DriveError("Google Drive 返回了无法识别的响应") from None
            error = payload.get("error") if isinstance(payload, dict) else None
            if response.status_code == 401 or (isinstance(error, dict) and error.get("code") == 401):
                if attempt == 0:
                    self._refresh()
                    continue
                raise AuthError("Google Drive 授权已失效，请重新获取 refresh_token")
            if response.status_code >= 400 or error:
                message = error.get("message") if isinstance(error, dict) else ""
                raise DriveError(message or "Google Drive 请求失败，请检查配置")
            return payload
        raise DriveError("Google Drive 请求失败，请检查配置")

    def check(self):
        payload = self._get("/about", {"fields": "user"})
        name = (payload.get("user") or {}).get("displayName") or ""
        return f"已连接：{name}" if name else "已连接"

    def list(self, directory):
        result, page = [], ""
        while True:
            payload = self._get("/files", {
                "q": f"'{directory.id}' in parents and trashed = false", "fields": FIELDS,
                "pageSize": "1000", "orderBy": "folder,name", "pageToken": page,
            })
            for item in payload.get("files") or []:
                mime, target = item.get("mimeType") or "", item.get("shortcutDetails") or {}
                if mime == SHORTCUT and target.get("targetId"):
                    mime, item = target.get("targetMimeType") or "", {**item, "id": target["targetId"]}
                if mime.startswith("application/vnd.google-apps.") and mime != FOLDER:
                    continue  # 在线文档无法作为媒体读取
                result.append(Entry(
                    name=item.get("name") or "", id=item.get("id") or "", is_dir=mime == FOLDER,
                    size=int(item.get("size") or 0), modified=item.get("modifiedTime") or "",
                    created=item.get("createdTime") or "",
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            page = payload.get("nextPageToken") or ""
            if not page:
                return result

    def link(self, file):
        self._get(f"/files/{file.id}", {"fields": "id"})  # 顺带确认 access_token 有效，必要时续期
        return Link(url=f"{API}/files/{file.id}?alt=media&acknowledgeAbuse=true&supportsAllDrives=true",
                    headers={"Authorization": f"Bearer {self.config['access_token']}"},
                    proxy=True, concurrency=0, part_size=0, expires_in=1800)
