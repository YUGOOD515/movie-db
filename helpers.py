import json
import logging
import os
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests


alist_url = os.environ.get("ALIST_ORIGIN", "http://127.0.0.1:5244").rstrip("/")
tmdb_url = os.environ.get("TMDB_API_ORIGIN", "https://api.themoviedb.org/3").rstrip(
    "/"
)
language = "zh-CN"
tmdb_image_url = "https://image.tmdb.org/t/p"

mpc_path = r"C:\Program Files\MPC-HC\mpc-hc64.exe"
mpc_web_url = "http://127.0.0.1:13579"

REQUEST_TIMEOUT = 20
MPC_REQUEST_TIMEOUT = 2
MPC_START_TIMEOUT = 30
MPC_POLL_INTERVAL = 5
MPC_FAILURE_LIMIT = 3
IMAGE_DOWNLOAD_ATTEMPTS = 3
IMAGE_TYPES = ("posters", "logos", "backdrops")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".svg"}
MEDIA_ENDPOINTS = {"movie": "movie", "tv": "tv"}
MEDIA_LABELS = {"movie": "电影", "tv": "剧集"}
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "ffprobe")
FFPROBE_TIMEOUT = int(os.environ.get("FFPROBE_TIMEOUT", "60"))

logger = logging.getLogger(__name__)
_playback_lock = threading.Lock()
_playback_generation = 0


def _tmdb_headers():
    token = os.environ.get("TMDB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("未配置 TMDB_TOKEN。")
    if not token.casefold().startswith("bearer "):
        token = f"Bearer {token}"
    return {
        "accept": "application/json",
        "Authorization": token,
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


def get_video_link(
    path: str,
    token: str | None = None,
    *,
    path_password: str = "",
):
    """Get the raw URL for a file stored in AList."""
    if token is None:
        alist_username = os.environ.get("ALIST_USERNAME", "").strip()
        alist_password = os.environ.get("ALIST_PASSWORD", "")
        if not alist_username or not alist_password:
            raise RuntimeError("未配置 ALIST_USERNAME 或 ALIST_PASSWORD。")
        token = get_token(alist_username, alist_password)
    response = requests.post(
        alist_url + "/api/fs/get",
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


def _probe_integer(value, *, positive: bool = False):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    minimum = 1 if positive else 0
    return parsed if parsed >= minimum else None


def _probe_frame_rate(value):
    if not value or value == "0/0":
        return None

    try:
        frame_rate = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None

    return round(frame_rate, 3) if frame_rate > 0 else None


def _probe_dynamic_range(video_stream: dict):
    side_data = video_stream.get("side_data_list") or []
    side_data_types = " ".join(
        str(item.get("side_data_type") or "") for item in side_data
    ).casefold()
    codec_tag = str(video_stream.get("codec_tag_string") or "").casefold()
    color_transfer = str(video_stream.get("color_transfer") or "").casefold()

    if "dovi" in side_data_types or codec_tag in {"dvh1", "dvhe"}:
        return "Dolby Vision"
    if "smpte2094-40" in side_data_types:
        return "HDR10+"
    if color_transfer == "smpte2084":
        return "HDR10 / PQ"
    if color_transfer == "arib-std-b67":
        return "HLG"

    return None


def probe_video(video_url: str):
    """Read stable media parameters from a local file or URL with ffprobe."""
    command = [
        FFPROBE_PATH,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        video_url,
    ]
    run_options = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": FFPROBE_TIMEOUT,
        "check": False,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_options["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(command, **run_options)
    except FileNotFoundError as error:
        raise ValueError(
            "未找到 ffprobe，请安装 FFmpeg 或设置 FFPROBE_PATH。"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("ffprobe 读取媒体参数超时。") from error

    if result.returncode != 0:
        raise ValueError("ffprobe 读取媒体参数失败。")

    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("ffprobe 返回了无效的 JSON 数据。") from error

    streams = payload.get("streams") or []
    media_format = payload.get("format") or {}
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    duration = media_format.get("duration") or video_stream.get("duration")
    try:
        media_duration_ms = max(round(float(duration) * 1000), 0)
    except (TypeError, ValueError):
        media_duration_ms = None

    return {
        "media_duration_ms": media_duration_ms,
        "file_size_bytes": _probe_integer(media_format.get("size")),
        "bit_rate": _probe_integer(media_format.get("bit_rate")),
        "container_format": media_format.get("format_name") or None,
        "video_codec": video_stream.get("codec_name") or None,
        "video_width": _probe_integer(video_stream.get("width"), positive=True),
        "video_height": _probe_integer(video_stream.get("height"), positive=True),
        "video_frame_rate": _probe_frame_rate(
            video_stream.get("avg_frame_rate")
            or video_stream.get("r_frame_rate")
        ),
        "video_pixel_format": video_stream.get("pix_fmt") or None,
        "video_dynamic_range": _probe_dynamic_range(video_stream),
        "audio_codec": audio_stream.get("codec_name") or None,
        "audio_channels": _probe_integer(
            audio_stream.get("channels"),
            positive=True,
        ),
        "audio_channel_layout": audio_stream.get("channel_layout") or None,
    }


def get_mpc_status():
    """Return the current MPC-HC Web Interface playback status."""
    response = requests.get(
        mpc_web_url + "/status.json",
        timeout=MPC_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    status = response.json()

    return {
        "file": str(status.get("file") or ""),
        "path": str(status.get("path") or ""),
        "state": int(status.get("state", -1)),
        "position_ms": max(int(status.get("position") or 0), 0),
        "duration_ms": max(int(status.get("duration") or 0), 0),
    }


def _format_mpc_position(position_ms: int):
    position_ms = max(int(position_ms), 0)
    total_seconds, milliseconds = divmod(position_ms, 1000)
    total_minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def seek_mpc(position_ms: int):
    """Seek the active MPC-HC item through its local Web Interface."""
    response = requests.get(
        mpc_web_url + "/command.html",
        params={
            "wm_command": "-1",
            "position": _format_mpc_position(position_ms),
        },
        timeout=MPC_REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def _normalized_media_path(value: str):
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    return unquote(path).replace("\\", "/").rstrip("/").casefold()


def _status_matches_video(status: dict, video_url: str):
    target_path = _normalized_media_path(video_url)
    current_path = _normalized_media_path(status["path"])
    if current_path:
        return current_path == target_path

    target_name = target_path.rsplit("/", 1)[-1]
    current_name = str(status["file"]).casefold()
    return bool(target_name and current_name == target_name)


def _current_playback_generation():
    with _playback_lock:
        return _playback_generation


def _next_playback_generation():
    global _playback_generation
    with _playback_lock:
        _playback_generation += 1
        return _playback_generation


def _monitor_mpc_playback(
    generation: int,
    video_url: str,
    start_position_ms: int,
    progress_callback,
):
    start_deadline = time.monotonic() + MPC_START_TIMEOUT
    matched_video = False
    playback_ready = False
    seeked = start_position_ms <= 0
    last_saved = None
    failures = 0

    while generation == _current_playback_generation():
        try:
            status = get_mpc_status()
        except (requests.RequestException, ValueError, TypeError):
            failures += 1
            if matched_video and failures >= MPC_FAILURE_LIMIT:
                return
            if not matched_video and time.monotonic() >= start_deadline:
                logger.warning("MPC-HC Web Interface did not become ready in time")
                return
            time.sleep(1 if not matched_video else MPC_POLL_INTERVAL)
            continue

        failures = 0
        if not _status_matches_video(status, video_url):
            if matched_video:
                return
            if time.monotonic() >= start_deadline:
                logger.warning("MPC-HC did not load the requested video in time")
                return
            time.sleep(1)
            continue

        matched_video = True
        duration_ms = status["duration_ms"]
        if duration_ms > 0:
            playback_ready = True
        if not playback_ready:
            if time.monotonic() >= start_deadline:
                logger.warning("MPC-HC loaded the video but did not report its duration")
                return
            time.sleep(1)
            continue

        if not seeked and duration_ms > 0:
            try:
                seek_mpc(min(start_position_ms, duration_ms))
            except requests.RequestException:
                logger.exception("Seeking MPC-HC to the saved position failed")
            seeked = True
            time.sleep(1)
            continue

        position_ms = status["position_ms"]
        progress = (position_ms, duration_ms)
        if duration_ms > 0 and position_ms > 0 and progress != last_saved:
            try:
                progress_callback(position_ms, duration_ms)
            except Exception:
                logger.exception("Saving MPC-HC playback progress failed")
                return
            last_saved = progress

        if status["state"] == 0:
            return
        time.sleep(MPC_POLL_INTERVAL)


def play_video(
    video_url: str,
    start_position_ms: int = 0,
    progress_callback=None,
):
    """Play a video and optionally synchronize progress through MPC-HC."""
    generation = _next_playback_generation()
    process = subprocess.Popen([mpc_path, video_url])
    if progress_callback is not None:
        monitor = threading.Thread(
            target=_monitor_mpc_playback,
            args=(
                generation,
                video_url,
                max(int(start_position_ms), 0),
                progress_callback,
            ),
            daemon=True,
            name="mpc-progress-monitor",
        )
        monitor.start()

    return process


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
    """Get TMDB supplemental details and the required IMDb identity."""
    endpoint = _media_endpoint(media_type)
    details = _tmdb_get(
        f"/{endpoint}/{media_id}",
        {"language": language},
    )
    if not details.get("imdb_id"):
        details["imdb_id"] = get_media_external_ids(media_type, media_id).get(
            "imdb_id"
        )
    return details


def get_media_external_ids(media_type: str, media_id: int) -> dict:
    """Get external IDs (such as imdb_id) from TMDB for a movie or TV series."""
    endpoint = _media_endpoint(media_type)
    try:
        payload = _tmdb_get(f"/{endpoint}/{media_id}/external_ids")
        if not isinstance(payload, dict):
            raise ValueError("TMDB external_ids response is not an object")
        return payload
    except (requests.RequestException, ValueError) as error:
        logger.warning(
            "Failed to fetch TMDB external IDs for %s %s: %s",
            media_type,
            media_id,
            error,
        )
        raise ValueError(
            f"Failed to fetch TMDB external IDs for {media_type} {media_id}: {error}"
        ) from error


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
    title_id: str,
    image_links: dict,
    image_root: str | Path,
):
    """Download the first five images per category for a movie or TV series."""
    return _download_image_collection(
        image_links,
        image_root,
        Path(title_id),
    )


def download_tv_episode_still(
    series_title_id: str,
    episode_title_id: str,
    still_path: str,
    image_root: str | Path,
):
    """Download the episode's still and return its local path record."""
    if not still_path:
        return None

    image_url = build_image_url(still_path)
    suffix = _image_suffix(still_path)

    relative_path = (
        Path("images")
        / series_title_id
        / f"{episode_title_id}-still{suffix}"
    )
    target_path = Path(image_root) / Path(*relative_path.parts[1:])

    try:
        _download_to_target(image_url, target_path)
    except (requests.RequestException, ValueError) as error:
        raise ValueError("TMDB 单集剧照下载失败。") from error

    return {
        "episode_id": episode_title_id,
        "local_path": relative_path.as_posix(),
    }
