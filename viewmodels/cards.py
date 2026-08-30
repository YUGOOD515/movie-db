from __future__ import annotations

from flask import url_for

from services.playback import is_effectively_completed


TITLE_TYPE_LABELS = {
    "movie": "电影",
    "series": "剧集",
    "episode": "单集",
}


def format_year(item: dict) -> str:
    start_year = item.get("start_year")
    end_year = item.get("end_year")
    if not start_year:
        return ""
    if item.get("title_type") == "series" and end_year and end_year != start_year:
        return f"{start_year}-{end_year}"
    return str(start_year)


def format_duration_ms(duration_ms: int | None) -> str:
    if not duration_ms:
        return ""
    total_minutes = max(round(duration_ms / 60_000), 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def format_position_ms(position_ms: int | None) -> str:
    total_seconds = max(int(position_ms or 0) // 1000, 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def build_progress(position_ms: int | None, duration_ms: int | None) -> dict:
    position = max(int(position_ms or 0), 0)
    duration = max(int(duration_ms or 0), 0)
    ratio = min(position / duration, 1) if duration else 0
    return {
        "position_ms": position,
        "duration_ms": duration,
        "ratio": ratio,
        "percent": round(ratio * 100, 1),
        "position_label": format_position_ms(position),
        "completed": is_effectively_completed(position, duration),
    }


def detail_url(item: dict) -> str:
    values = {"title_id": item["title_id"]}
    if item.get("season_number") is not None:
        values["season"] = item["season_number"]
    if item.get("episode_number") is not None:
        values["episode"] = item["episode_number"]
    return url_for("title_detail", **values)


def build_poster_card(item: dict, *, show_rating: bool = False) -> dict:
    return {
        "title_id": item["title_id"],
        "title": item["display_title"],
        "type": item["title_type"],
        "type_label": TITLE_TYPE_LABELS.get(item["title_type"], item["title_type"]),
        "year": format_year(item),
        "poster": item.get("poster_path"),
        "backdrop": item.get("backdrop_path"),
        "logo": item.get("logo_path"),
        "subtitle": format_year(item),
        "progress": None,
        "play_url": None,
        "rating": item.get("average_rating") if show_rating else None,
        "detail_url": detail_url(item),
    }


def format_file_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return ""
    size = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            precision = 0 if unit == "B" else 2
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return ""


def format_bit_rate(bit_rate: int | None) -> str:
    if not bit_rate:
        return ""
    if bit_rate >= 1_000_000:
        return f"{bit_rate / 1_000_000:.2f} Mbps"
    return f"{bit_rate / 1000:.0f} kbps"


def format_frame_rate(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):g} fps"
    except (TypeError, ValueError):
        return str(value)


def resolution_label(width: int | None, height: int | None) -> str:
    width = int(width or 0)
    height = int(height or 0)
    if width >= 3840 or height >= 2160:
        return "4K"
    if width >= 2560 or height >= 1440:
        return "2K"
    if width >= 1920 or height >= 1080:
        return "1080P"
    if width >= 1280 or height >= 720:
        return "720P"
    if width or height:
        return f"{width}×{height}"
    return ""


def build_media_badges(source: dict | None) -> list[dict]:
    if not source:
        return []
    dynamic_range = str(source.get("video_dynamic_range") or "").strip()
    dynamic_kind = (
        "dolby_vision"
        if any(
            token in dynamic_range.casefold()
            for token in ("dolby vision", "dolby", "dv")
        )
        else "text"
    )
    candidates = [
        (
            "text",
            resolution_label(
                source.get("video_width"), source.get("video_height")
            ),
        ),
        ("text", str(source.get("video_codec") or "").upper()),
        ("text", format_frame_rate(source.get("video_frame_rate"))),
        ("text", format_file_size(source.get("file_size_bytes"))),
        (dynamic_kind, dynamic_range),
        ("text", source.get("audio_channel_layout") or ""),
    ]
    badges = []
    labels = set()
    for kind, candidate in candidates:
        label = str(candidate).strip()
        normalized = label.casefold()
        if label and normalized not in labels:
            badges.append({"kind": kind, "label": label})
            labels.add(normalized)
    return badges


def build_source(source: dict) -> dict:
    progress = build_progress(
        source.get("position_ms"),
        source.get("duration_ms"),
    )
    return {
        **source,
        "play_url": url_for("play_media_file", file_id=source["file_id"]),
        "duration_label": format_duration_ms(source.get("duration_ms")),
        "file_size_label": format_file_size(source.get("file_size_bytes")),
        "bit_rate_label": format_bit_rate(source.get("bit_rate")),
        "frame_rate_label": format_frame_rate(source.get("video_frame_rate")),
        "resolution_label": resolution_label(
            source.get("video_width"), source.get("video_height")
        ),
        "badges": build_media_badges(source),
        "progress": progress,
    }
