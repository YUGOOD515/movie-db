import logging
import os
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse

import requests


logger = logging.getLogger(__name__)
MPC_REQUEST_TIMEOUT = 2
MPC_START_TIMEOUT = 30
MPC_POLL_INTERVAL = 5
MPC_FAILURE_LIMIT = 3
_playback_lock = threading.Lock()
_playback_generation = 0


def _mpc_web_url() -> str:
    return os.environ.get("MPC_WEB_URL", "http://127.0.0.1:13579").rstrip("/")


def get_mpc_status() -> dict:
    response = requests.get(
        _mpc_web_url() + "/status.json", timeout=MPC_REQUEST_TIMEOUT
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


def _format_mpc_position(position_ms: int) -> str:
    total_seconds, milliseconds = divmod(max(int(position_ms), 0), 1000)
    total_minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def seek_mpc(position_ms: int) -> None:
    response = requests.get(
        _mpc_web_url() + "/command.html",
        params={"wm_command": "-1", "position": _format_mpc_position(position_ms)},
        timeout=MPC_REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def _normalized_media_path(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    return unquote(path).replace("\\", "/").rstrip("/").casefold()


def _status_matches_video(status: dict, video_url: str) -> bool:
    target_path = _normalized_media_path(video_url)
    current_path = _normalized_media_path(status["path"])
    if current_path:
        return current_path == target_path
    target_name = target_path.rsplit("/", 1)[-1]
    return bool(target_name and str(status["file"]).casefold() == target_name)


def _current_playback_generation() -> int:
    with _playback_lock:
        return _playback_generation


def _next_playback_generation() -> int:
    global _playback_generation
    with _playback_lock:
        _playback_generation += 1
        return _playback_generation


def _monitor_mpc_playback(
    generation: int,
    video_url: str,
    start_position_ms: int,
    progress_callback,
) -> None:
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

        if not seeked:
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
    generation = _next_playback_generation()
    player_path = os.environ.get(
        "MPC_PATH", r"C:\Program Files\MPC-HC\mpc-hc64.exe"
    )
    process = subprocess.Popen([player_path, video_url])
    if progress_callback is not None:
        monitor = threading.Thread(
            target=_monitor_mpc_playback,
            args=(generation, video_url, max(int(start_position_ms), 0), progress_callback),
            daemon=True,
            name="mpc-progress-monitor",
        )
        monitor.start()
    return process

