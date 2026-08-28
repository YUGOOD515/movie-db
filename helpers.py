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
MEDIA_ENDPOINTS = {"movie": "movie", "tv": "tv"}
MEDIA_LABELS = {"movie": "电影", "tv": "剧集"}

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


def _media_endpoint(media_type: str):
    try:
        return MEDIA_ENDPOINTS[media_type]
    except KeyError as error:
        raise ValueError("媒体类型无效。") from error


def search_media(media_type: str, name: str):
    """Search TMDB for a movie or TV series by name."""
    endpoint = _media_endpoint(media_type)
    return _tmdb_get(
        f"/search/{endpoint}",
        {
            "query": name,
            "include_adult": "true",
            "language": language,
            "page": 1,
        },
    )


def get_media_details(media_type: str, media_id: int):
    """Get TMDB details for a movie or TV series."""
    endpoint = _media_endpoint(media_type)
    return _tmdb_get(
        f"/{endpoint}/{media_id}",
        {"language": language},
    )


def find_media(media_type: str, name: str):
    """Return details for the first TMDB match of the requested media type."""
    results = search_media(media_type, name).get("results", [])
    label = MEDIA_LABELS.get(media_type, "媒体")
    if not results:
        raise ValueError(f"TMDB 中未找到对应{label}。")

    media_id = int(results[0]["id"])
    details = get_media_details(media_type, media_id)
    if int(details.get("id", 0)) != media_id:
        raise ValueError(f"TMDB {label}详情返回异常。")

    return details


def search_tv_season(series_id: int, season_number: int):
    """Get season metadata and every episode in that season from TMDB."""
    return _tmdb_get(
        f"/tv/{series_id}/season/{season_number}",
        {"language": language},
    )


def get_tv_episode(season_details: dict, episode_number: int):
    """Return one numbered episode from a TMDB season payload."""
    for episode in season_details.get("episodes", []):
        if int(episode.get("episode_number", -1)) == episode_number:
            return episode

    raise ValueError("TMDB 中未找到指定季和集数。")


def parse_tv_position(season_value: str, episode_value: str):
    """Validate form season/episode values and return integers."""
    try:
        season_number = int(season_value)
        episode_number = int(episode_value)
    except (TypeError, ValueError) as error:
        raise ValueError("Season 和 Episode 必须填写整数。") from error

    if season_number < 0 or episode_number < 1:
        raise ValueError("Season 必须大于等于 0，Episode 必须大于等于 1。")

    return season_number, episode_number


def build_image_url(file_path: str | None, size: str = "original"):
    """Build a complete TMDB image URL from an image file path."""
    if not file_path:
        return None

    return f"{tmdb_image_url}/{size}/{file_path.lstrip('/')}"


def search_media_images(media_type: str, media_id: int):
    """Get Chinese posters/logos and landscape backdrops for media."""
    endpoint = _media_endpoint(media_type)
    images = _tmdb_get(
        f"/{endpoint}/{media_id}/images",
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


def _image_suffix(image_url_or_path: str):
    suffix = Path(urlparse(image_url_or_path).path).suffix.lower()
    return suffix if suffix in IMAGE_SUFFIXES else ".jpg"


def _download_to_target(image_url: str, target_path: Path):
    temporary_path = target_path.with_suffix(target_path.suffix + ".part")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _download_image(image_url, temporary_path)
        temporary_path.replace(target_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _collection_image_paths(
    image_root: Path,
    relative_directory: Path,
    image_type: str,
    position: int,
    image_url: str,
):
    filename = f"{position}{_image_suffix(image_url)}"
    relative_path = (
        Path("images") / relative_directory / image_type / filename
    )
    target_path = image_root / relative_directory / image_type / filename
    return relative_path, target_path


def _download_image_collection(
    image_links: dict,
    image_root: str | Path,
    relative_directory: str | Path,
):
    image_root = Path(image_root)
    relative_directory = Path(relative_directory)
    records = []

    for image_type in IMAGE_TYPES:
        for position, image_url in enumerate(image_links.get(image_type, [])[:5], 1):
            relative_path, target_path = _collection_image_paths(
                image_root,
                relative_directory,
                image_type,
                position,
                image_url,
            )

            try:
                _download_to_target(image_url, target_path)
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


def download_media_images(
    media_type: str,
    media_id: int,
    image_links: dict,
    image_root: str | Path,
):
    """Download the first five images per category for a movie or TV series."""
    _media_endpoint(media_type)
    relative_directory = (
        Path("tv") / str(media_id)
        if media_type == "tv"
        else Path(str(media_id))
    )
    return _download_image_collection(
        image_links,
        image_root,
        relative_directory,
    )


def download_tv_episode_still(
    series_id: int,
    season_number: int,
    episode: dict,
    image_root: str | Path,
):
    """Download the selected episode's still and return its local path record."""
    still_path = episode.get("still_path")
    if not still_path:
        return None

    episode_id = int(episode["id"])
    image_url = build_image_url(still_path)
    suffix = _image_suffix(still_path)

    relative_path = (
        Path("images")
        / "tv"
        / str(series_id)
        / f"season-{season_number}"
        / "episodes"
        / f"{episode_id}{suffix}"
    )
    target_path = Path(image_root) / Path(*relative_path.parts[1:])

    try:
        _download_to_target(image_url, target_path)
    except (requests.RequestException, ValueError) as error:
        raise ValueError("TMDB 单集剧照下载失败。") from error

    return {
        "episode_id": episode_id,
        "local_path": relative_path.as_posix(),
    }


def jsonp(data):
    """Pretty-print JSON-compatible data for local debugging."""
    print(json.dumps(data, indent=4, ensure_ascii=False))

    return True
