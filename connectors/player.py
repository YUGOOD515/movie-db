"""调用本机播放器播放 AList 链接，并尽量记录观看进度。

- MPC-HC（Windows 默认）：通过它的 Web 接口读取进度、跳转到上次位置；
- IINA / mpv（macOS、Linux 默认）：通过 mpv 的 JSON IPC 读取进度，用 ``--start`` 从上次位置播放；
- VLC、系统默认程序：只负责打开，不记录进度。

``PLAYER`` 选择播放器（留空自动选择本机已安装的），``PLAYER_PATH`` 指定程序路径；
MPC-HC 沿用 ``MPC_PATH`` 与 ``MPC_WEB_URL``。
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests


logger = logging.getLogger(__name__)
IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
DEFAULT_MPC_PATH = r"C:\Program Files\MPC-HC\mpc-hc64.exe"
PLAYER_NAMES = {"mpc-hc": "MPC-HC", "iina": "IINA", "mpv": "mpv", "vlc": "VLC", "system": "系统默认程序"}
# 能记录观看进度的播放器
TRACKS_PROGRESS = {"mpc-hc", "iina", "mpv"}
IPC_POLL_INTERVAL = 3
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


class PlayerNotFound(RuntimeError):
    pass


def label(kind: str) -> str:
    return PLAYER_NAMES.get(kind, "自定义播放器")


def _auto_order() -> tuple[str, ...]:
    if IS_WINDOWS:
        return ("mpc-hc", "mpv", "vlc")
    if IS_MACOS:
        return ("iina", "mpv", "vlc")
    return ("mpv", "vlc")


def _candidates(kind: str, env=os.environ) -> list[str]:
    """各播放器的常见安装位置（按优先顺序）。"""
    home = Path.home()
    which = lambda *names: [found for name in names if (found := shutil.which(name))]  # noqa: E731
    if kind == "mpc-hc":
        return [(env.get("MPC_PATH") or "").strip() or DEFAULT_MPC_PATH,
                r"C:\Program Files (x86)\MPC-HC\mpc-hc.exe", *which("mpc-hc64", "mpc-hc")]
    if kind == "iina":
        return [str(base / "IINA.app" / "Contents" / "MacOS" / "iina-cli")
                for base in (Path("/Applications"), home / "Applications")] + which("iina")
    if kind == "mpv":
        return [*which("mpv"), "/opt/homebrew/bin/mpv", "/usr/local/bin/mpv",
                "/Applications/mpv.app/Contents/MacOS/mpv", str(home / "Applications" / "mpv.app" / "Contents" / "MacOS" / "mpv"),
                r"C:\Program Files\mpv\mpv.exe"]
    if kind == "vlc":
        return [*which("vlc"), "/Applications/VLC.app/Contents/MacOS/VLC",
                r"C:\Program Files\VideoLAN\VLC\vlc.exe", r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"]
    return []


def _executable(path: str, kind: str = "") -> str | None:
    """把用户填写的路径换成可执行文件；macOS 允许直接填 .app。"""
    target = Path(path).expanduser()
    if target.suffix == ".app" and target.is_dir():
        if kind == "iina" or target.name.casefold() == "iina.app":
            target = target / "Contents" / "MacOS" / "iina-cli"
        else:
            import plistlib

            try:
                info = plistlib.loads((target / "Contents" / "Info.plist").read_bytes())
                target = target / "Contents" / "MacOS" / str(info["CFBundleExecutable"])
            except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
                return None
    return str(target) if target.is_file() else None


def _guess_kind(path: str) -> str:
    name = path.replace("\\", "/").casefold()
    for kind, keys in (("iina", ("iina",)), ("mpc-hc", ("mpc-hc", "mpc-be")), ("mpv", ("mpv",)), ("vlc", ("vlc",))):
        if any(key in name for key in keys):
            return kind
    return "custom"


def _find(kind: str, env=os.environ) -> str | None:
    custom = (env.get("PLAYER_PATH") or "").strip()
    if custom and kind != "mpc-hc":
        return _executable(custom, kind)
    return next((path for path in _candidates(kind, env) if path and Path(path).is_file()), None)


def resolve_player(overrides: dict | None = None) -> tuple[str, str | None]:
    """返回 (播放器, 程序路径)；系统默认程序没有路径。找不到指定的播放器时抛出 PlayerNotFound。

    ``overrides`` 用于设置页检测尚未保存的值。
    """
    env = {**os.environ, **(overrides or {})}
    choice = (env.get("PLAYER") or "").strip().casefold()
    custom = (env.get("PLAYER_PATH") or "").strip()
    if choice in ("", "auto") and custom:
        path = _executable(custom)
        if not path:
            raise PlayerNotFound("播放器程序路径无效，请在设置 → 播放器中检查。")
        return _guess_kind(path), path
    if choice in ("", "auto"):
        for kind in _auto_order():
            path = _find(kind, env)
            if path:
                return kind, path
        return "system", None
    if choice not in PLAYER_NAMES:
        raise PlayerNotFound(f"不认识的播放器：{choice}")
    if choice == "system":
        return "system", None
    path = _find(choice, env)
    if not path:
        raise PlayerNotFound(f"没有找到 {PLAYER_NAMES[choice]}，请在设置 → 播放器中填写程序路径。")
    return choice, path


def _popen(command: list[str]) -> subprocess.Popen:
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True, **({} if IS_WINDOWS else {"start_new_session": True}))


def _open_with_system(video_url: str):
    if IS_WINDOWS:
        os.startfile(video_url)  # noqa: S606
        return None
    return _popen(["open" if IS_MACOS else "xdg-open", video_url])


def _ipc_path(generation: int) -> str:
    # macOS 的 Unix 套接字路径不能超过 104 字节，放在系统临时目录下
    return str(Path(tempfile.gettempdir()) / f"moviedb-mpv-{os.getpid()}-{generation}.sock")


def play_video(
    video_url: str,
    start_position_ms: int = 0,
    progress_callback=None,
):
    generation = _next_playback_generation()
    kind, path = resolve_player()
    start_seconds = max(int(start_position_ms), 0) / 1000
    if kind == "mpc-hc":
        process = _popen([path, video_url])
        monitor = _monitor_mpc_playback
        args = (generation, video_url, max(int(start_position_ms), 0), progress_callback)
    elif kind in ("mpv", "iina"):
        ipc = None if IS_WINDOWS else _ipc_path(generation)
        if kind == "mpv":
            command = [path, "--force-window=immediate", f"--start={start_seconds:.3f}"]
            command += [f"--input-ipc-server={ipc}"] if ipc else []
        else:  # iina-cli：mpv 选项加 --mpv- 前缀
            command = [path, f"--mpv-start={start_seconds:.3f}"]
            command += [f"--mpv-input-ipc-server={ipc}"] if ipc else []
        process = _popen([*command, video_url])
        monitor = _monitor_mpv_playback if ipc else None
        args = (generation, ipc, progress_callback)
    elif kind == "vlc":
        process = _popen([path, f"--start-time={start_seconds:.0f}", video_url])
        monitor = None
    elif kind == "custom":
        process = _popen([path, video_url])
        monitor = None
    else:
        process = _open_with_system(video_url)
        monitor = None
    if progress_callback is not None and monitor is not None:
        threading.Thread(target=monitor, args=args, daemon=True, name=f"{kind}-progress-monitor").start()
    return process


# ------------------------------------------------------------ mpv / IINA：JSON IPC


class _MpvClient:
    def __init__(self, path: str):
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.settimeout(MPC_REQUEST_TIMEOUT)
        self._socket.connect(path)
        self._reader = self._socket.makefile("r", encoding="utf-8")
        self._request = 0

    def get(self, name: str):
        self._request += 1
        message = {"command": ["get_property", name], "request_id": self._request}
        self._socket.sendall((json.dumps(message) + "\n").encode("utf-8"))
        while True:
            line = self._reader.readline()
            if not line:
                raise ConnectionError("播放器已关闭")
            reply = json.loads(line)
            if reply.get("request_id") == self._request:  # 其余是事件通知
                return reply.get("data") if reply.get("error") == "success" else None

    def close(self) -> None:
        for item in (self._reader, self._socket):
            try:
                item.close()
            except OSError:
                pass


def _monitor_mpv_playback(generation: int, ipc_path: str, progress_callback) -> None:
    deadline = time.monotonic() + MPC_START_TIMEOUT
    client = None
    last_saved = None
    try:
        while generation == _current_playback_generation():
            if client is None:
                try:
                    client = _MpvClient(ipc_path)
                except OSError:
                    if time.monotonic() >= deadline:
                        logger.warning("Player IPC did not become ready in time: %s", ipc_path)
                        return
                    time.sleep(0.5)
                    continue
            try:
                position, duration = client.get("time-pos"), client.get("duration")
            except (OSError, ValueError):
                return  # 播放器已退出
            if position and duration:
                progress = (int(float(position) * 1000), int(float(duration) * 1000))
                if progress != last_saved and progress[0] > 0:
                    try:
                        progress_callback(*progress)
                    except Exception:
                        logger.exception("Saving playback progress failed")
                        return
                    last_saved = progress
            time.sleep(IPC_POLL_INTERVAL)
    finally:
        if client is not None:
            client.close()
        try:
            Path(ipc_path).unlink(missing_ok=True)
        except OSError:
            pass
