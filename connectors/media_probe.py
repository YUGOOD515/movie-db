import json
import os
import subprocess
import re
from fractions import Fraction


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


def probe_video(video_url: str) -> dict:
    command = [
        os.environ.get("FFPROBE_PATH", "ffprobe"),
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
        detail = result.stderr or ""
        code = "expired" if re.search(r"\b(?:401|403)\b|expired|forbidden", detail, re.I) else "busy" if re.search(r"\b(?:429|503)\b|too many|频繁", detail, re.I) else "invalid"
        raise ProbeError("ffprobe 读取媒体参数失败。", code=code, retryable=code == "busy")

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

