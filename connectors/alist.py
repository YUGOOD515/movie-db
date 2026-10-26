import os
import re
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import requests

from connectors import throttle


REQUEST_TIMEOUT = 20
VIDEO_EXTENSIONS = frozenset({
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv",
})


# AList 登录令牌默认 48 小时有效；进程内缓存 1 小时，避免每个文件都重新登录。
TOKEN_TTL = 3600
_token_cache: dict = {}
_token_lock = threading.Lock()
# 网盘驱动风控时 AList 仍返回 HTTP 200，只在 message 里说明，例如夸克的“请求太频繁”。
_THROTTLED_MESSAGE = re.compile(r"频繁|过快|稍后再试|too many|rate.?limit|\b429\b", re.IGNORECASE)


def configured_token() -> str:
    username = os.environ.get("ALIST_USERNAME", "").strip()
    password = os.environ.get("ALIST_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("未配置 ALIST_USERNAME 或 ALIST_PASSWORD。")
    key = (get_origin(), username, password)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        token = get_token(username, password)
        _token_cache.clear()
        _token_cache[key] = (token, time.monotonic() + TOKEN_TTL)
        return token


def forget_token() -> None:
    with _token_lock:
        _token_cache.clear()


def _post(endpoint: str, body: dict, token: str | None = None) -> requests.Response:
    headers = {"Authorization": token} if token else None
    return throttle.call(
        "alist",
        lambda: requests.post(get_origin() + endpoint, headers=headers, json=body, timeout=REQUEST_TIMEOUT),
        throttled=_is_throttled,
    )


def _is_throttled(response) -> bool:
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return (isinstance(payload, dict) and payload.get("code") not in (None, 200)
            and bool(_THROTTLED_MESSAGE.search(str(payload.get("message") or ""))))


def get_file(path: str, token: str) -> dict:
    """一次 fs/get 同时拿到签名（用于 /d/ 播放链接）与 raw_url（用于 ffprobe 读取）。"""
    data = _response_data(_post(
        "/api/fs/get",
        {"path": path, "password": os.environ.get("ALIST_PATH_PASSWORD", "")},
        token,
    ))
    if data.get("is_dir") or not data.get("sign"):
        raise ValueError("无法获取视频签名直链，请检查 AList 文件权限及签名设置。")
    return {
        "signed_url": get_origin() + "/d" + quote(path, safe="/") + "?" + urlencode({"sign": data["sign"]}),
        "raw_url": data.get("raw_url") or "",
        "size": data.get("size"),
        "modified": data.get("modified"),
    }


def _response_data(response: requests.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") == 401:
        forget_token()
    if payload.get("code") not in (None, 200) or payload.get("data") is None:
        raise RuntimeError(payload.get("message") or "AList 请求失败。")
    return payload["data"]


def get_origin() -> str:
    return os.environ.get("ALIST_ORIGIN", "http://127.0.0.1:5244").rstrip("/")


def get_token(alist_username: str, alist_password: str) -> str:
    response = _post("/api/auth/login", {"username": alist_username, "password": alist_password})
    return _response_data(response)["token"]


def list_directory(
    path: str,
    token: str,
    *,
    path_password: str = "",
    refresh: bool = False,
) -> list[dict]:
    response = _post(
        "/api/fs/list",
        {"path": path, "password": path_password, "page": 1, "per_page": -1, "refresh": refresh},
        token,
    )
    return _response_data(response)["content"] or []


def get_video_link(
    path: str,
    token: str | None = None,
    *,
    path_password: str = "",
) -> str:
    if token is None:
        token = configured_token()
    response = _post(
        "/api/fs/get",
        {"path": path, "password": path_password, "page": 1, "per_page": 0, "refresh": False},
        token,
    )
    return _response_data(response)["raw_url"]


def parse_alist_url(value: str) -> str:
    candidate = urlparse(value.strip())
    expected = urlparse(get_origin())

    try:
        candidate_port = candidate.port or (443 if candidate.scheme == "https" else 80)
        expected_port = expected.port or (443 if expected.scheme == "https" else 80)
    except ValueError as error:
        raise ValueError("AList 链接端口无效。") from error

    if (
        candidate.scheme != expected.scheme
        or candidate.hostname != expected.hostname
        or candidate_port != expected_port
        or candidate.username
        or candidate.password
    ):
        raise ValueError(f"AList 链接必须来自 {get_origin()}。")
    if candidate.fragment:
        raise ValueError("AList 视频直链不能包含锚点。")
    if not candidate.path.startswith("/d/"):
        raise ValueError("请输入路径以 /d/ 开头的 AList 视频直链。")

    try:
        query = parse_qs(candidate.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ValueError("AList 视频直链的查询参数无效。") from error
    if set(query) != {"sign"} or len(query["sign"]) != 1 or not query["sign"][0]:
        raise ValueError("AList 视频直链必须包含有效的 sign 参数。")

    alist_path = unquote(candidate.path[2:]).rstrip("/") or "/"
    if alist_path == "/" or any(part in (".", "..") for part in alist_path.split("/")):
        raise ValueError("请输入具体且有效的 AList 文件路径。")
    return alist_path


def validate_alist_video_url(value: str) -> None:
    with throttle.call("alist", lambda: requests.get(
        value,
        headers={"Range": "bytes=0-0"},
        stream=True,
        timeout=REQUEST_TIMEOUT,
    )) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if not (
            content_type.startswith("video/")
            or content_type.startswith("application/octet-stream")
        ):
            raise ValueError("AList 直链返回的资源不是视频。")
