import json
import logging
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests


alist_url = "http://127.0.0.1:5244"
username = "admin"
password = "***REMOVED***"

tmdb_url = "https://api.themoviedb.org/3"
tmdb_token = "***REMOVED***"
language = "zh-CN"
tmdb_image_url = "https://image.tmdb.org/t/p"

mpc_path = r"C:\Program Files\MPC-HC\mpc-hc64.exe"

REQUEST_TIMEOUT = 20
IMAGE_DOWNLOAD_ATTEMPTS = 3
IMAGE_TYPES = ("posters", "logos", "backdrops")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".svg"}

logger = logging.getLogger(__name__)


def _tmdb_headers():
    return {
        "accept": "application/json",
        "Authorization": tmdb_token,
    }


def _tmdb_get(endpoint: str, params: dict | None = None):
    response = requests.get(
        tmdb_url + endpoint,
        headers=_tmdb_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    return response.json()


def get_token(alist_username: str, alist_password: str):
    """Get an AList authorization token."""
    response = requests.post(
        alist_url + "/api/auth/login",
        json={
            "username": alist_username,
            "password": alist_password,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    return response.json()["data"]["token"]


def get_video_link(path: str):
    """Get the raw URL for a file stored in AList."""
    response = requests.post(
        alist_url + "/api/fs/get",
        headers={"Authorization": get_token(username, password)},
        json={
            "path": path,
            "password": password,
            "page": 1,
            "per_page": 0,
            "refresh": False,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    return response.json()["data"]["raw_url"]


def play_video(video_url: str):
    """Play a video URL with MPC-HC."""
    subprocess.Popen([mpc_path, video_url])


def tmdb_auth():
    """Check whether the configured TMDB token is valid."""
    return bool(_tmdb_get("/authentication").get("success"))


def tmbd_auth():
    """Backward-compatible alias for the original misspelled function name."""
    return tmdb_auth()


def search_movie(name: str):
    """Search TMDB for movies by name."""
    return _tmdb_get(
        "/search/movie",
        {
            "query": name,
            "include_adult": "true",
            "language": language,
            "page": 1,
        },
    )


def search_id(movie_id: int):
    """Get a movie's TMDB details by ID."""
    return _tmdb_get(
        f"/movie/{movie_id}",
        {"language": language},
    )


def build_image_url(file_path: str | None, size: str = "original"):
    """Build a complete TMDB image URL from an image file path."""
    if not file_path:
        return None

    return f"{tmdb_image_url}/{size}/{file_path.lstrip('/')}"


def search_img(movie_id: int):
    """Get Chinese posters/logos and landscape backdrops for a movie."""
    images = _tmdb_get(
        f"/movie/{movie_id}/images",
        {"include_image_language": "zh,en,null"},
    )

    return {
        "posters": [
            build_image_url(image.get("file_path"), "w500")
            for image in images.get("posters", [])
            if image.get("file_path") and image.get("iso_639_1") == "zh"
        ],
        "logos": [
            build_image_url(image.get("file_path"))
            for image in images.get("logos", [])
            if image.get("file_path") and image.get("iso_639_1") == "zh"
        ],
        "backdrops": [
            build_image_url(image.get("file_path"))
            for image in images.get("backdrops", [])
            if image.get("file_path")
        ],
    }


def parse_alist_url(value: str):
    """Validate an AList direct URL and return its unsigned virtual path."""
    candidate = urlparse(value.strip())
    expected = urlparse(alist_url)

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
        raise ValueError(f"AList 链接必须来自 {alist_url}。")

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


def build_alist_url(alist_path: str):
    """Build a browser URL from a stored AList virtual path."""
    return alist_url.rstrip("/") + quote(alist_path, safe="/")


def validate_alist_video_url(value: str):
    """Verify an AList direct URL as a video without downloading its content."""
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


def _download_image(image_url: str, temporary_path: Path):
    last_error = None

    for attempt in range(1, IMAGE_DOWNLOAD_ATTEMPTS + 1):
        try:
            with requests.get(
                image_url,
                stream=True,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    raise ValueError("TMDB 返回的资源不是图片。")

                with temporary_path.open("wb") as image_file:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            image_file.write(chunk)

            return
        except (requests.RequestException, ValueError) as error:
            temporary_path.unlink(missing_ok=True)
            last_error = error
            if attempt == IMAGE_DOWNLOAD_ATTEMPTS:
                raise
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    raise last_error


def download_movie_images(movie_id: int, image_links: dict, image_root: str | Path):
    """Download the first five images per category and return database records."""
    image_root = Path(image_root)
    records = []

    for image_type in IMAGE_TYPES:
        for position, image_url in enumerate(image_links.get(image_type, [])[:5], 1):
            suffix = Path(urlparse(image_url).path).suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                suffix = ".jpg"

            relative_path = (
                Path("images")
                / str(movie_id)
                / image_type
                / f"{position}{suffix}"
            )
            target_path = image_root / str(movie_id) / image_type / f"{position}{suffix}"
            temporary_path = target_path.with_suffix(target_path.suffix + ".part")
            target_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                _download_image(image_url, temporary_path)
                temporary_path.replace(target_path)
            except (requests.RequestException, ValueError) as error:
                logger.warning(
                    "Skipping TMDB %s image %s after %s attempts: %s",
                    image_type,
                    position,
                    IMAGE_DOWNLOAD_ATTEMPTS,
                    error,
                )
                continue

            records.append(
                {
                    "image_type": image_type,
                    "image_position": position,
                    "image_url": image_url,
                    "local_path": relative_path.as_posix(),
                }
            )

    if not records and any(image_links.get(image_type) for image_type in IMAGE_TYPES):
        raise ValueError("TMDB 图片均下载失败。")

    return records


def jsonp(data):
    """Pretty-print JSON-compatible data for local debugging."""
    print(json.dumps(data, indent=4, ensure_ascii=False))

    return True
