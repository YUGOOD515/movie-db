"""OneDrive（Microsoft Graph）：按条目 ID 列目录与取链接，下载地址自带授权、约 1 小时有效。

参照 OpenList drivers/onedrive 改写。默认通过 OpenList 令牌服务续期；填了自己的 client_id / client_secret
时直接向微软续期。
"""

from __future__ import annotations

import threading
from urllib.parse import quote

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link, renew_online

HOSTS = {
    "global": ("https://login.microsoftonline.com", "https://graph.microsoft.com"),
    "cn": ("https://login.chinacloudapi.cn", "https://microsoftgraph.chinacloudapi.cn"),
    "us": ("https://login.microsoftonline.us", "https://graph.microsoft.us"),
    "de": ("https://login.microsoftonline.de", "https://graph.microsoft.de"),
}
SELECT = "id,name,size,fileSystemInfo,file,folder"


class OneDriveDrive(Drive):
    kind = "onedrive"
    label = "OneDrive"
    oauth_txt = "onedrive_pr"   # OpenList 授权页的驱动标识
    oauth_embedded = True       # 桌面版在 MovieDB 窗口内完成授权并自动读取结果
    fields = (
        Field("refresh_token", "refresh_token", "secret", required=True, manual=True),
        Field("region", "版本", "select", default="global", advanced=True,
              options=(("global", "国际版"), ("cn", "世纪互联"), ("us", "美国政府版"), ("de", "德国版"))),
        Field("client_id", "client_id", "secret", advanced=True, hint="留空则通过 OpenList 的令牌服务续期"),
        Field("client_secret", "client_secret", "secret", advanced=True, hint="留空则通过 OpenList 的令牌服务续期"),
        Field("redirect_uri", "回调地址", default="https://api.oplist.org/onedrive/callback", advanced=True),
        Field("renew_api", "续期服务", default="https://api.oplist.org/onedrive/renewapi", advanced=True),
        Field("root_path", "根目录路径", default="/", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.oauth, self.graph = HOSTS.get(config.get("region") or "global", HOSTS["global"])
        self.root_path = "/" + str(config.get("root_path") or "/").strip("/")
        self._root = ""
        self._lock = threading.Lock()

    def _refresh(self):
        with self._lock:
            if self.config.get("client_id") and self.config.get("client_secret"):
                response = throttle.call("onedrive", lambda: self.session.post(
                    self.oauth + "/common/oauth2/v2.0/token", timeout=20, data={
                        "grant_type": "refresh_token", "refresh_token": self.config.get("refresh_token", ""),
                        "client_id": self.config["client_id"], "client_secret": self.config["client_secret"],
                        "redirect_uri": self.config.get("redirect_uri", ""),
                    }))
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                access, refresh = payload.get("access_token") or "", payload.get("refresh_token") or ""
                if not access or not refresh:
                    raise AuthError("OneDrive refresh_token 已失效，请重新获取")
            else:
                access, refresh = renew_online(
                    self.session, "onedrive", self.config.get("renew_api") or "https://api.oplist.org/onedrive/renewapi",
                    self.config.get("refresh_token", ""), "onedrive_pr", "OneDrive")
            self.config.update(access_token=access, refresh_token=refresh)
            self.save(self.config)

    def _get(self, url):
        for attempt in range(2):
            if not self.config.get("access_token"):
                self._refresh()
            response = throttle.call("onedrive", lambda: self.session.get(
                url, headers={"Authorization": f"Bearer {self.config['access_token']}"}, timeout=20))
            try:
                payload = response.json()
            except ValueError:
                raise DriveError("OneDrive 返回了无法识别的响应") from None
            error = (payload.get("error") or {}) if isinstance(payload, dict) else {}
            if response.status_code == 401 or error.get("code") == "InvalidAuthenticationToken":
                if attempt == 0:
                    self._refresh()
                    continue
                raise AuthError("OneDrive 授权已失效，请重新获取 refresh_token")
            if response.status_code == 404:
                raise DriveError("OneDrive 中找不到这个目录")
            if response.status_code >= 400 or error:
                raise DriveError(error.get("message") or "OneDrive 请求失败，请检查配置")
            return payload
        raise DriveError("OneDrive 请求失败，请检查配置")

    def root(self):
        if not self._root:
            if self.root_path == "/":
                self._root = "root"
            else:
                self._root = self._get(f"{self.graph}/v1.0/me/drive/root:{quote(self.root_path)}?$select=id")["id"]
        return Entry(name="", is_dir=True, id=self._root)

    def check(self):
        payload = self._get(f"{self.graph}/v1.0/me/drive")
        self.root()
        owner = ((payload.get("owner") or {}).get("user") or {}).get("displayName") or ""
        return f"已连接：{owner}" if owner else "已连接"

    def list(self, directory):
        result = []
        url = f"{self.graph}/v1.0/me/drive/items/{directory.id}/children?$top=1000&$select={SELECT}"
        while url:
            payload = self._get(url)
            for item in payload.get("value") or []:
                info = item.get("fileSystemInfo") or {}
                result.append(Entry(
                    name=item.get("name") or "", id=item.get("id") or "", is_dir="folder" in item,
                    size=item.get("size") or 0, modified=info.get("lastModifiedDateTime") or "",
                    created=info.get("createdDateTime") or "",
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            url = payload.get("@odata.nextLink") or ""
        return result

    def link(self, file):
        payload = self._get(f"{self.graph}/v1.0/me/drive/items/{file.id}?$select=id,@microsoft.graph.downloadUrl")
        url = payload.get("@microsoft.graph.downloadUrl") or ""
        if not url:
            raise DriveError("无法获取播放链接")
        return Link(url=url, proxy=True, concurrency=0, part_size=0, expires_in=3000)
