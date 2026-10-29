from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse

from connectors.drives import DRIVERS
from connectors.drives.base import DriveError, Entry, Link
from models import drive_store
from services import paths

VIDEO_EXTENSIONS = frozenset({
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv",
})
CACHE_TTL = 1800

_lock = threading.RLock()
_drives = {}
_directories = {}
_links = {}
_link_locks = {}
_clock = time.monotonic
_key_lock = threading.Lock()


def split(path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("路径必须以 / 开头。")
    if path == "/":
        return "", []
    parts = path.split("/")[1:]
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("路径包含无效片段。")
    return parts[0], parts[1:]


def drive_for(mount):
    with _lock:
        if mount in _drives:
            return _drives[mount]
        row = drive_store.get_drive(mount)
        if not row or not row["enabled"]:
            raise FileNotFoundError(f"找不到挂载：{mount}")
        driver = DRIVERS.get(row["kind"])
        if driver is None:
            raise DriveError(f"不支持的网盘类型：{row['kind']}")
        drive = driver(row["config"], lambda config: drive_store.update_config(mount, config))
        _drives[mount] = drive
        return drive


def _select(path):
    mount, parts = split(path)
    row = drive_store.get_drive(mount) if mount else None
    if row and row["enabled"]:
        return row["mount"], drive_for(row["mount"]), parts
    fallback = drive_store.get_drive("*")
    if fallback and fallback["enabled"]:
        full_parts = path.split("/")[1:]
        return "*", drive_for("*"), full_parts
    raise FileNotFoundError(f"找不到挂载：{mount}")


def _list(drive, mount, directory, refresh=False):
    key = (mount, directory.id)
    now = _clock()
    with _lock:
        cached = _directories.get(key)
        if cached and not refresh and cached[0] > now:
            return cached[1]
    entries = drive.list(directory)
    with _lock:
        _directories[key] = (now + CACHE_TTL, entries)
    return entries


def resolve(path, *, refresh=False):
    mount, drive, parts = _select(path)
    entry = drive.root()
    for part in parts:
        match = next((item for item in _list(drive, mount, entry, refresh)
                      if item.name == part), None)
        if match is None:
            raise FileNotFoundError(f"找不到：{path}")
        entry = match
    return drive, entry


def _public(entry):
    return {key: getattr(entry, key) for key in ("name", "is_dir", "size", "modified", "created")}


def list_directory(path, *, refresh=False):
    if path == "/":
        result = {}
        for row in drive_store.list_drives():
            if row["enabled"] and row["mount"] != "*":
                result[row["mount"]] = {
                    "name": row["mount"], "is_dir": True, "size": 0, "modified": "", "created": "",
                }
        fallback = drive_store.get_drive("*")
        if fallback and fallback["enabled"]:
            drive = drive_for("*")
            for entry in _list(drive, "*", drive.root(), refresh):
                result.setdefault(entry.name, _public(entry))
        return list(result.values())
    drive, entry = resolve(path, refresh=refresh)
    if not entry.is_dir:
        raise DriveError("不是目录")
    mount, _, _ = _select(path)
    return [_public(item) for item in _list(drive, mount, entry, refresh)]


def get_file(path):
    try:
        _, entry = resolve(path)
    except FileNotFoundError:
        _, entry = resolve(path, refresh=True)
    if entry.is_dir:
        raise DriveError("目录不能作为文件播放")
    url = stream_url(path)
    return {"signed_url": url, "raw_url": url, "size": entry.size, "modified": entry.modified}


def open_link(path, *, fresh=False):
    with _lock:
        lock = _link_locks.setdefault(path, threading.Lock())
    with lock:
        now = _clock()
        if fresh:
            with _lock:
                _links.pop(path, None)
        with _lock:
            cached = _links.get(path)
            if cached and cached[0] > now:
                return cached[1], cached[2]
        drive, entry = resolve(path)
        if entry.is_dir:
            raise DriveError("目录不能作为文件播放")
        link = drive.link(entry)
        ttl = CACHE_TTL if link.expires_in is None else min(link.expires_in, CACHE_TTL)
        with _lock:
            _links[path] = (now + ttl, link, entry)
        return link, entry


def forget(mount=None):
    with _lock:
        if mount is None:
            _drives.clear()
            _directories.clear()
            _links.clear()
            _link_locks.clear()
            return
        _drives.pop(mount, None)
        for key in list(_directories):
            if key[0] == mount:
                _directories.pop(key, None)
        _links.clear()


def _stream_key():
    path = paths.DATA_DIR / "stream.key"
    with _key_lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(secrets.token_bytes(32))
        return path.read_bytes()


def _signature(path):
    return hmac.new(_stream_key(), path.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def stream_url(path):
    encoded = base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")
    token = f"{encoded}.{_signature(path)}"
    name = PurePosixPath(path).name
    port = os.environ.get("MOVIEDB_PORT", "5000")
    return f"http://127.0.0.1:{port}/stream/{token}/{quote(name)}"


def parse_stream_token(token):
    try:
        encoded, signature = token.rsplit(".", 1)
        path = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ValueError("播放地址无效") from None
    if not hmac.compare_digest(signature, _signature(path)):
        raise ValueError("播放地址无效")
    return path


def parse_source(value):
    value = value.strip()
    if value.startswith("/"):
        split(value)
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or "/d/" not in parsed.path:
        raise ValueError("请输入挂载路径或 AList 直链。")
    path = "/" + unquote(parsed.path.split("/d/", 1)[1])
    split(path)
    return path


def migrate_alist():
    username = os.environ.get("ALIST_USERNAME", "").strip()
    password = os.environ.get("ALIST_PASSWORD", "")
    if not username or not password or drive_store.get_drive("*"):
        return False
    drive_store.save_drive("*", "alist", {
        "origin": os.environ.get("ALIST_ORIGIN", "http://127.0.0.1:5244"),
        "username": username,
        "password": password,
        "path_password": os.environ.get("ALIST_PATH_PASSWORD", ""),
    }, True)
    return True
