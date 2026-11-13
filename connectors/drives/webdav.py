"""WebDAV（群晖、坚果云、Alist 的 /dav 等）：PROPFIND 列目录，Basic 认证直接读取文件。

条目 ID 是服务器上的绝对路径（已解码），播放时按需编码。
"""

from __future__ import annotations

import base64
import posixpath
import re
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link

DAV = "{DAV:}"
PROPFIND = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:displayname/><d:getcontentlength/><d:getlastmodified/><d:creationdate/><d:resourcetype/>"
            "</d:prop></d:propfind>")


def _time(value):
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value  # creationdate 是 ISO 8601，交给 Entry 统一


class WebDAVDrive(Drive):
    kind = "webdav"
    label = "WebDAV"
    fields = (
        Field("url", "地址", required=True, hint="例如 https://nas.local:5006/dav"),
        Field("username", "用户名", required=True),
        Field("password", "密码", "secret", required=True),
        Field("root_path", "根目录路径", default="/", advanced=True, hint="相对于上面的地址"),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        address = str(config.get("url") or "").strip().rstrip("/")
        parts = urlsplit(address)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("请填写以 http:// 或 https:// 开头的 WebDAV 地址")
        self.origin = f"{parts.scheme}://{parts.netloc}"
        base = unquote(parts.path or "/")
        root = "/" + str(config.get("root_path") or "/").strip("/")
        self.root_id = posixpath.normpath(posixpath.join(base, root.lstrip("/"))) if root != "/" else (base or "/")
        token = base64.b64encode(f"{config.get('username', '')}:{config.get('password', '')}".encode()).decode()
        self.auth = {"Authorization": f"Basic {token}"}

    def _url(self, path, directory=False):
        path = quote(path or "/")
        return self.origin + (path if not directory or path.endswith("/") else path + "/")

    def _propfind(self, path, depth):
        response = throttle.call("drive", lambda: self.session.request(
            "PROPFIND", self._url(path, directory=True), data=PROPFIND.encode(),
            headers={**self.auth, "Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
            timeout=20,
        ))
        if response.status_code == 401:
            raise AuthError("WebDAV 用户名或密码错误")
        if response.status_code == 404:
            raise DriveError("WebDAV 目录不存在，请检查地址与根目录路径")
        if response.status_code != 207:
            raise DriveError(f"WebDAV 请求失败（HTTP {response.status_code}）")
        # multistatus 不需要 DOCTYPE；拒绝它即杜绝实体声明（XXE、实体膨胀）
        if re.search(rb"<!(DOCTYPE|ENTITY)", response.content, re.IGNORECASE):
            raise DriveError("WebDAV 返回了无法识别的响应")
        try:
            return ElementTree.fromstring(response.content)
        except ElementTree.ParseError:
            raise DriveError("WebDAV 返回了无法识别的响应") from None

    def check(self):
        self._propfind(self.root_id, 0)
        return f"已连接：{self.origin}"

    def list(self, directory):
        current = directory.id.rstrip("/") or "/"
        result = []
        for node in self._propfind(current, 1).iter(f"{DAV}response"):
            href = node.findtext(f"{DAV}href") or ""
            path = unquote(urlsplit(href).path).rstrip("/") or "/"
            if path == current:
                continue
            prop = node.find(f".//{DAV}prop")
            if prop is None:
                continue
            is_dir = prop.find(f"{DAV}resourcetype/{DAV}collection") is not None
            name = prop.findtext(f"{DAV}displayname") or posixpath.basename(path)
            try:
                size = int(prop.findtext(f"{DAV}getcontentlength") or 0)
            except ValueError:
                size = 0
            result.append(Entry(
                name=name, id=path, is_dir=is_dir, size=size,
                modified=_time(prop.findtext(f"{DAV}getlastmodified")),
                created=_time(prop.findtext(f"{DAV}creationdate")),
            ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
        return result

    def link(self, file):
        return Link(url=self._url(file.id), headers=dict(self.auth), proxy=True,
                    concurrency=0, part_size=0, expires_in=None)
