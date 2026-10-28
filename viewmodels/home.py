from collections import OrderedDict

from flask import url_for

from viewmodels.cards import (
    TITLE_TYPE_LABELS,
    build_poster_card,
    build_progress,
    detail_url,
    format_year,
)


GENRE_ICONS = {
    "Action": "zap",
    "Adventure": "compass",
    "Animation": "palette",
    "Comedy": "laugh",
    "Crime": "fingerprint",
    "Drama": "drama",
    "Family": "users",
    "Fantasy": "sparkles",
    "Horror": "eye",
    "Music": "music",
    "Musical": "music",
    "Mystery": "circle-help",
    "Romance": "heart",
    "Sci-Fi": "rocket",
    "Thriller": "activity",
}

# Existing databases retain their original Bootstrap icon names.
COLLECTION_ICONS = {
    "collection": "library", "collection-play": "library",
    "heart": "heart", "heart-fill": "heart", "bookmark-heart": "heart",
    "bookmark": "bookmark", "bookmark-fill": "bookmark", "bookmark-star": "bookmark",
    "star": "star", "star-fill": "star", "film": "film", "tv": "tv",
}


def build_recent_watch(rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        is_series = row["title_type"] == "series"
        progress = build_progress(
            row.get("position_ms"), row.get("duration_ms")
        )
        subtitle = format_year(row)
        if is_series:
            subtitle = (
                f"S{int(row['season_number'] or 0)}"
                f" E{int(row['episode_number'] or 0)}"
                f" · {row['episode_title']}"
            )
        detail_values = {"title_id": row["title_id"]}
        if is_series:
            detail_values["resume"] = 1
        items.append(
            {
                "title": row["display_title"],
                "subtitle": subtitle,
                "logo": row.get("logo_path"),
                "backdrop": row.get("backdrop_path"),
                "rating": None,
                "progress": progress,
                "detail_url": url_for("title_detail", **detail_values),
                "play_url": (
                    url_for("resume_title", title_id=row["title_id"])
                    if is_series
                    else url_for("play_media_file", file_id=row["file_id"])
                ),
            }
        )
    return items


def build_collection_cards(rows: list[dict]) -> list[dict]:
    return [
        {
            **row,
            "icon": COLLECTION_ICONS.get(row.get("icon_name"), "library"),
            "detail_url": url_for(
                "collection_detail", collection_id=row["collection_id"]
            ),
        }
        for row in rows
    ]


def build_genre_cards(rows: list[dict], title_type: str) -> list[dict]:
    grouped = OrderedDict()
    for row in rows:
        for genre in (row.get("genres") or "").split(","):
            genre = genre.strip()
            if not genre:
                continue
            entry = grouped.setdefault(
                genre,
                {
                    "name": genre,
                    "count": 0,
                    "backdrop": row.get("backdrop_path"),
                    "representative_title": row["display_title"],
                },
            )
            entry["count"] += 1
            if not entry["backdrop"] and row.get("backdrop_path"):
                entry["backdrop"] = row["backdrop_path"]

    cards = []
    for entry in grouped.values():
        entry["icon"] = GENRE_ICONS.get(entry["name"], "tag")
        entry["detail_url"] = url_for(
            "catalog", type=title_type, genre=entry["name"]
        )
        cards.append(entry)
    return sorted(cards, key=lambda item: (-item["count"], item["name"]))


def format_runtime(minutes: int | None) -> str:
    if not minutes:
        return ""
    hours, rest = divmod(int(minutes), 60)
    return f"{hours} 小时 {rest} 分钟" if hours and rest else (f"{hours} 小时" if hours else f"{rest} 分钟")


def build_featured(rows: list[dict]) -> list[dict]:
    """首页主视觉轮播：整幅背景图、标志、简介和播放入口。"""
    items = []
    for row in rows:
        is_series = row["title_type"] == "series"
        play_url, play_label = None, None
        if is_series and row.get("has_episode_files"):
            play_url, play_label = url_for("resume_title", title_id=row["title_id"]), "继续播放"
        elif not is_series and row.get("file_id"):
            play_url, play_label = url_for("play_media_file", file_id=row["file_id"]), "播放"
        genres = [genre.strip() for genre in (row.get("genres") or "").split(",") if genre.strip()]
        items.append(
            {
                "title": row["display_title"],
                "type_label": TITLE_TYPE_LABELS.get(row["title_type"], row["title_type"]),
                "year": format_year(row),
                "runtime": "" if is_series else format_runtime(row.get("runtime_minutes")),
                "genres": genres[:3],
                "rating": row.get("average_rating"),
                "tagline": row.get("tagline") or "",
                "overview": row.get("overview") or "",
                "backdrop": row.get("backdrop_path"),
                "logo": row.get("logo_path"),
                "detail_url": detail_url(row),
                "play_url": play_url,
                "play_label": play_label,
            }
        )
    return items


def build_home_viewmodel(raw: dict) -> dict:
    return {
        "featured": build_featured(raw.get("featured", [])),
        "recent_watch": build_recent_watch(raw["recent_watch"]),
        "collections": build_collection_cards(raw["collections"]),
        "recent_added": [build_poster_card(row) for row in raw["recent_added"]],
        "movie_genres": build_genre_cards(raw["movie_genres"], "movie"),
        "series_genres": build_genre_cards(raw["series_genres"], "series"),
        "unwatched": [build_poster_card(row) for row in raw["unwatched"]],
        "top_movies": [
            build_poster_card(row, show_rating=True)
            for row in raw["top_movies"]
        ],
    }
