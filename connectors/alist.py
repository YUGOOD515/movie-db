import os
from urllib.parse import parse_qs, unquote, urlparse

import requests


REQUEST_TIMEOUT = 20


def get_origin() -> str:
    return os.environ.get("ALIST_ORIGIN", "http://127.0.0.1:5244").rstrip("/")


def get_token(alist_username: str, alist_password: str) -> str:
    response = requests.post(
        get_origin() + "/api/auth/login",
        json={"username": alist_username, "password": alist_password},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["data"]["token"]


def get_video_link(
    path: str,
    token: str | None = None,
    *,
    path_password: str = "",
) -> str:
    if token is None:
        username = os.environ.get("ALIST_USERNAME", "").strip()
        password = os.environ.get("ALIST_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("未配置 ALIST_USERNAME 或 ALIST_PASSWORD。")
        token = get_token(username, password)

    response = requests.post(
        get_origin() + "/api/fs/get",
        headers={"Authorization": token},
        json={
            "path": path,
            "password": path_password,
            "page": 1,
            "per_page": 0,
            "refresh": False,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["data"]["raw_url"]


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
    with requests.get(
        value,
        headers={"Range": "bytes=0-0"},
        stream=True,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if not (
            content_type.startswith("video/")
            or content_type.startswith("application/octet-stream")
        ):
            raise ValueError("AList 直链返回的资源不是视频。")

