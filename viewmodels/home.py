from collections import OrderedDict

from flask import url_for

from viewmodels.cards import build_poster_card, build_progress, format_year


GENRE_ICONS = {
    "Action": "lightning-charge",
    "Adventure": "compass",
    "Animation": "palette",
    "Comedy": "emoji-laughing",
    "Crime": "fingerprint",
    "Drama": "masks-theater",
    "Family": "people",
    "Fantasy": "stars",
    "Horror": "eye",
    "Music": "music-note-beamed",
    "Musical": "music-note-beamed",
    "Mystery": "question-diamond",
    "Romance": "heart",
    "Sci-Fi": "rocket-takeoff",
    "Thriller": "activity",
}


def build_recent_watch(rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        is_series = row["title_type"] == "series"
        subtitle = format_year(row)
        if is_series:
            subtitle = (
                f"S{int(row['season_number'] or 0):02d}"
                f"E{int(row['episode_number'] or 0):02d}"
                f" · {row['episode_title']}"
            )
        detail_values = {"title_id": row["title_id"]}
        if is_series:
            detail_values.update(
                season=row["season_number"], episode=row["episode_number"]
            )
        items.append(
            {
                "title": row["display_title"],
                "subtitle": subtitle,
                "logo": row.get("logo_path"),
                "backdrop": row.get("backdrop_path"),
                "rating": None,
                "progress": build_progress(
                    row.get("position_ms"), row.get("duration_ms")
                ),
                "detail_url": url_for("title_detail", **detail_values),
                "play_url": url_for(
                    "play_media_file", file_id=row["file_id"]
                ),
            }
        )
    return items


def build_collection_cards(rows: list[dict]) -> list[dict]:
    return [
        {
            **row,
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


def build_home_viewmodel(raw: dict) -> dict:
    return {
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
