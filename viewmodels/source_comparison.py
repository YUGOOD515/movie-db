"""Present playable versions in the play menu and the full metadata comparison."""

from __future__ import annotations
from viewmodels.media_labels import chip, codec as _codec, channels as _channels, dynamic_range


def _duration(value) -> str:
    if not value or value <= 0:
        return ""
    seconds = round(value / 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def build_source_options(sources: list[dict]) -> list[dict]:
    """Build aligned parameter rows, keeping unknown values in the same position."""
    options = []
    names = [str(source.get("alist_path") or "").replace("\\", "/").rsplit("/", 1)[-1] for source in sources]
    for index, source in enumerate(sources, start=1):
        path = str(source.get("alist_path") or "").replace("\\", "/")
        resolution = source.get("resolution_label") or ""
        options.append({
            **source,
            "source_id": source["file_id"],
            "source_name": (path if names.count(names[index - 1]) > 1 else names[index - 1]) or "未知来源",
            "comparison_values": {
                "duration": _duration(source.get("duration_ms")),
                "resolution": resolution,
                "video_codec": _codec(source.get("video_codec")),
                "frame_rate": source.get("frame_rate_label") or "",
                "dynamic_range": dynamic_range(source.get("video_dynamic_range")),
                "audio_codec": _codec(source.get("audio_codec")),
                "channels": _channels(source),
                "size": source.get("file_size_label") or "",
                "container": str(source.get("container_format") or "").strip().upper(),
            },
        })
    fields = [
        ("duration", "时长"), ("resolution", "清晰度"), ("video_codec", "视频编码"),
        ("frame_rate", "帧率"), ("dynamic_range", "动态范围"),
        ("audio_codec", "音频编码"), ("channels", "声道"),
    ]
    differing, common = [], []
    for key, label in fields:
        values = {option["comparison_values"][key] for option in options}
        if len(values) > 1:
            differing.append((key, label))
        elif values and "" not in values:
            common.append(chip(key, label, next(iter(values))))
    # Identical technical parameters still need distinguishable file choices.
    if not differing:
        for key, label in (("size", "大小"), ("container", "容器")):
            if len({option["comparison_values"][key] for option in options}) > 1:
                differing.append((key, label))
    parameter_fields = [
        (key, label) for key, label in fields + [("size", "大小"), ("container", "容器")]
        if any(option["comparison_values"][key] for option in options)
    ]
    for option in options:
        values = option.pop("comparison_values")
        option["comparison_fields"] = [
            chip(key, label, values[key])
            for key, label in differing
        ]
        option["common_metadata"] = common
        option["parameters"] = [
            chip(key, label, values[key])
            for key, label in parameter_fields
        ]
    return options


def build_media_versions(sources: list[dict], featured_source: dict | None) -> list[dict]:
    """Keep all file values, including unknowns, in matching rows for each version."""
    versions = []
    featured_id = featured_source.get("file_id") if featured_source else None
    for source in sources:
        width, height = source.get("video_width"), source.get("video_height")
        pixels = f"{width} × {height}" if width and height else ""
        tier = source.get("resolution_label") or ""
        resolution = " · ".join(value for value in (tier, pixels) if value)
        summary = [
            ("resolution", "分辨率", resolution),
            ("video_codec", "视频编码", _codec(source.get("video_codec"))),
            ("frame_rate", "帧率", source.get("frame_rate_label")),
            ("dynamic_range", "动态范围", dynamic_range(source.get("video_dynamic_range"))),
            ("audio_codec", "音频编码", _codec(source.get("audio_codec"))),
            ("channels", "声道", _channels(source)),
            ("duration", "时长", _duration(source.get("duration_ms"))),
            ("size", "文件大小", source.get("file_size_label")),
        ]
        details = [
            ("path", "文件路径", source.get("alist_path"), "folder"),
            ("container", "容器格式", source.get("container_format"), "file"),
            ("pixel_format", "像素格式", source.get("video_pixel_format"), "image"),
            ("bit_rate", "码率", source.get("bit_rate_label"), "gauge"),
            ("channel_layout", "声道布局", source.get("audio_channel_layout"), "volume"),
            ("probed_at", "探测时间", source.get("probed_at"), "clock"),
        ]
        is_featured = source["file_id"] == featured_id
        versions.append({
            "file_id": source["file_id"],
            "name": source["source_name"],
            "path": source.get("alist_path") or "未知路径",
            "play_url": source["play_url"],
            "status": ("最近播放" if source.get("watched_at") else "默认播放") if is_featured else "",
            "fields": [chip(key, label, value) for key, label, value in summary]
                      + [{**chip(key, label, value), "icon": icon}
                         for key, label, value, icon in details],
        })
    return versions
