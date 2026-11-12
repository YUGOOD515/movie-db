import json
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlparse


class ProbeError(ValueError):
    def __init__(self, message, *, code="invalid", retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


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


def candidates() -> list[str]:
    """按优先级列出 ffprobe：设置里的路径 → 安装包自带 → 系统 PATH。"""
    from services import paths

    configured = os.environ.get("FFPROBE_PATH", "").strip()
    found = [configured] if configured and configured != "ffprobe" else []
    if paths.BUNDLED_FFPROBE.is_file():
        found.append(str(paths.BUNDLED_FFPROBE))
    system = shutil.which("ffprobe")
    if system:
        found.append(system)
    return found


def executable() -> str:
    """第一个真实存在的 ffprobe；设置里的路径无效时退回自带或 PATH 中的版本（与设置页状态一致）。"""
    for candidate in candidates():
        if shutil.which(candidate) or Path(candidate).is_file():
            return candidate
    return os.environ.get("FFPROBE_PATH", "").strip() or "ffprobe"


def explain_failure(stderr: str, video_url: str = "") -> tuple[str, str]:
    """把 ffprobe 的错误输出翻译成可操作的说明（不回显地址里的签名令牌）；返回 (说明, 错误码)。"""
    if re.search(r"connection refused|failed to connect|actively refused|error number -138|拒绝", stderr, re.I):
        target = urlparse(video_url)
        where = f"（{target.hostname}:{target.port}）" if target.hostname and target.port else ""
        return f"连不上 MovieDB 播放地址{where}，请确认 MovieDB 仍在运行。", "connection"
    if re.search(r"\b(?:401|403)\b|expired|forbidden", stderr, re.I):
        return "网盘链接已过期或没有访问权限。", "expired"
    if re.search(r"\b(?:429|503)\b|too many|频繁", stderr, re.I):
        return "网盘请求过于频繁，稍后会重试。", "busy"
    if re.search(r"\b404\b|not found", stderr, re.I):
        return "网盘上找不到这个文件，可能已被移动或删除。", "missing"
    if re.search(r"invalid data found|moov atom not found|end of file", stderr, re.I):
        return "文件无法识别为视频，可能已损坏或不完整。", "invalid"
    lines = [re.sub(r"\S+://\S+", "", line).strip(" :") for line in stderr.splitlines() if line.strip()]
    detail = f"：{lines[-1][:120]}" if lines and lines[-1] else ""
    return f"ffprobe 读取媒体参数失败{detail}。", "invalid"


def probe_video(video_url: str) -> dict:
    command = [
        executable(),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        *(["-user_agent", "MovieDB-Probe"] if video_url.startswith(("http://", "https://")) else []),
        video_url,
    ]
    run_options = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": int(os.environ.get("FFPROBE_TIMEOUT", "60")),
        "check": False,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_options["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(command, **run_options)
    except FileNotFoundError as error:
        raise ProbeError("未找到 ffprobe，请安装 FFmpeg 或设置 FFPROBE_PATH。") from error
    except subprocess.TimeoutExpired as error:
        raise ProbeError("ffprobe 读取媒体参数超时。", code="timeout", retryable=True) from error
    if result.returncode != 0:
        message, code = explain_failure(result.stderr or "", video_url)
        raise ProbeError(message, code=code, retryable=code == "busy")

    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("ffprobe 返回了无效的 JSON 数据。") from error

    streams = payload.get("streams") or []
    media_format = payload.get("format") or {}
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), {}
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), {}
    )
    duration = media_format.get("duration") or video_stream.get("duration")
    try:
        duration_ms = max(round(float(duration) * 1000), 0)
    except (TypeError, ValueError):
        duration_ms = None

    return {
        "duration_ms": duration_ms,
        "file_size_bytes": _probe_integer(media_format.get("size")),
        "bit_rate": _probe_integer(media_format.get("bit_rate")),
        "container_format": media_format.get("format_name") or None,
        "video_codec": video_stream.get("codec_name") or None,
        "video_width": _probe_integer(video_stream.get("width"), positive=True),
        "video_height": _probe_integer(video_stream.get("height"), positive=True),
        "video_frame_rate": _probe_frame_rate(
            video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        ),
        "video_pixel_format": video_stream.get("pix_fmt") or None,
        "video_dynamic_range": _probe_dynamic_range(video_stream),
        "audio_codec": audio_stream.get("codec_name") or None,
        "audio_channels": _probe_integer(audio_stream.get("channels"), positive=True),
        "audio_channel_layout": audio_stream.get("channel_layout") or None,
    }

