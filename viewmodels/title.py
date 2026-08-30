from __future__ import annotations

from collections import defaultdict

from flask import url_for

from viewmodels.cards import (
    build_source,
    format_duration_ms,
    format_year,
)


SOURCE_FIELDS = (
    "file_id",
    "alist_path",
    "duration_ms",
    "file_size_bytes",
    "bit_rate",
    "container_format",
    "video_codec",
    "video_width",
    "video_height",
    "video_frame_rate",
    "video_pixel_format",
    "video_dynamic_range",
    "audio_codec",
    "audio_channels",
    "audio_channel_layout",
    "probed_at",
    "playback_id",
    "position_ms",
    "watched_at",
)


def _group_artwork(rows: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["artwork_type"]].append(row)
    return dict(grouped)


def _primary_artwork(artwork: dict, artwork_type: str) -> str | None:
    images = artwork.get(artwork_type, [])
    return images[0]["local_path"] if images else None


def _choose_source(sources: list[dict]) -> dict | None:
    if not sources:
        return None
    watched = [source for source in sources if source.get("watched_at")]
    if watched:
        return max(
            watched,
            key=lambda source: (
                source.get("watched_at") or "",
                source.get("playback_id") or 0,
            ),
        )
    return min(sources, key=lambda source: source["file_id"])


def _episode_source(row: dict) -> dict | None:
    if row.get("file_id") is None:
        return None
    return build_source({field: row.get(field) for field in SOURCE_FIELDS})


def _group_episodes(rows: list[dict], series_id: str) -> list[dict]:
    episodes = {}
    for row in rows:
        episode = episodes.setdefault(
            row["episode_id"],
            {
                "title_id": row["episode_id"],
                "tmdb_id": row.get("episode_tmdb_id"),
                "display_title": row["episode_display_title"],
                "primary_title": row.get("episode_primary_title"),
                "original_title": row.get("episode_original_title"),
                "original_language": row.get("episode_original_language"),
                "start_year": row.get("episode_start_year"),
                "runtime_minutes": row.get("episode_runtime_minutes"),
                "overview": row.get("episode_overview"),
                "average_rating": row.get("episode_average_rating"),
                "num_votes": row.get("episode_num_votes"),
                "season_number": row["season_number"],
                "episode_number": row["episode_number"],
                "still": row.get("still_path"),
                "sources": [],
            },
        )
        source = _episode_source(row)
        if source is not None:
            episode["sources"].append(source)

    ordered = sorted(
        episodes.values(),
        key=lambda episode: (
            episode["season_number"] if episode["season_number"] is not None else -1,
            episode["episode_number"] if episode["episode_number"] is not None else -1,
            episode["title_id"],
        ),
    )
    for episode in ordered:
        episode["featured_source"] = _choose_source(episode["sources"])
        source = episode["featured_source"]
        episode["progress"] = source["progress"] if source else None
        duration = source.get("duration_ms") if source else None
        episode["duration_label"] = (
            format_duration_ms(duration)
            or (
                f"{episode['runtime_minutes']} 分钟"
                if episode.get("runtime_minutes")
                else ""
            )
        )
        episode["detail_url"] = url_for(
            "title_detail",
            title_id=series_id,
            season=episode["season_number"],
            episode=episode["episode_number"],
        )
    return ordered


def _select_episode(
    episodes: list[dict],
    season_number: int | None,
    episode_number: int | None,
) -> dict | None:
    if not episodes:
        return None
    if season_number is not None and episode_number is not None:
        selected = next(
            (
                item
                for item in episodes
                if item["season_number"] == season_number
                and item["episode_number"] == episode_number
            ),
            None,
        )
        if selected:
            return selected
    if season_number is not None:
        selected = next(
            (
                item
                for item in episodes
                if item["season_number"] == season_number
            ),
            None,
        )
        if selected:
            return selected
    return next(
        (
            item
            for item in episodes
            if item["season_number"] == 1 and item["episode_number"] == 1
        ),
        episodes[0],
    )


def _build_seasons(episodes: list[dict], selected: dict | None) -> list[dict]:
    grouped = defaultdict(list)
    for episode in episodes:
        grouped[episode["season_number"]].append(episode)
    return [
        {
            "season_number": season_number,
            "episodes": season_episodes,
            "active": bool(
                selected and selected["season_number"] == season_number
            ),
        }
        for season_number, season_episodes in sorted(
            grouped.items(), key=lambda item: item[0] if item[0] is not None else -1
        )
    ]


def _favorite_state(
    raw: dict,
    *,
    title_id: str,
    collection_title_type: str,
) -> dict | None:
    eligible = [
        collection
        for collection in raw["collections"]
        if collection.get("title_type") in (None, collection_title_type)
    ]
    if not eligible:
        return None
    collection = next(
        (
            item
            for item in eligible
            if item.get("collection_type") == "system"
            and item.get("title_type") == collection_title_type
        ),
        eligible[0],
    )
    is_favorite = (collection["collection_id"], title_id) in raw["memberships"]
    return {
        "title_id": title_id,
        "collection_id": collection["collection_id"],
        "collection_name": collection["name"],
        "active": is_favorite,
        "action_url": url_for(
            "toggle_collection_item",
            collection_id=collection["collection_id"],
            title_id=title_id,
        ),
        "label": "取消收藏" if is_favorite else "加入收藏",
    }


def _frame_rate_label(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):g} fps"
    except (TypeError, ValueError):
        return str(value)


def _source_summary(source: dict | None) -> dict:
    source = source or {}
    dynamic_range = str(source.get("video_dynamic_range") or "")
    return {
        "resolution_label": source.get("resolution_label") or "",
        "video_codec_label": str(source.get("video_codec") or "").upper(),
        "frame_rate_label": _frame_rate_label(source.get("video_frame_rate")),
        "file_size_label": source.get("file_size_label") or "",
        "dynamic_range_label": dynamic_range,
        "is_dolby_vision": any(
            token in dynamic_range.casefold()
            for token in ("dolby vision", "dolby", "dv")
        ),
    }


def _media_facts(source: dict | None) -> list[dict]:
    if not source:
        return []
    summary = _source_summary(source)
    values = [
        ("文件路径", source.get("alist_path")),
        ("容器格式", source.get("container_format")),
        ("分辨率", summary["resolution_label"]),
        ("视频编码", summary["video_codec_label"]),
        ("像素格式", source.get("video_pixel_format")),
        ("帧率", summary["frame_rate_label"]),
        ("动态范围", summary["dynamic_range_label"]),
        ("音频编码", str(source.get("audio_codec") or "").upper()),
        ("声道", source.get("audio_channel_layout")),
        ("文件大小", source.get("file_size_label")),
        ("码率", source.get("bit_rate_label")),
    ]
    return [
        {"label": label, "value": value}
        for label, value in values
        if value not in (None, "")
    ]


def _legacy_source(source: dict, index: int) -> dict:
    labels = [
        source.get("resolution_label"),
        str(source.get("video_codec") or "").upper(),
        str(source.get("container_format") or "").upper(),
    ]
    return {
        **source,
        "source_id": source["file_id"],
        "source_label": " · ".join(label for label in labels if label)
        or f"版本 {index}",
    }


def _legacy_episode(episode: dict, backdrop_path: str | None) -> dict:
    sources = [
        _legacy_source(source, index)
        for index, source in enumerate(episode["sources"], start=1)
    ]
    selected_file_id = (
        episode["featured_source"]["file_id"]
        if episode.get("featured_source")
        else None
    )
    featured_source = next(
        (source for source in sources if source["file_id"] == selected_file_id),
        sources[0] if sources else None,
    )
    progress = episode.get("progress") or {}
    has_progress = bool(progress.get("position_ms"))
    if has_progress and not progress.get("completed"):
        play_label = f"继续播放 {progress['position_label']}"
    else:
        play_label = (
            f"播放 S{episode['season_number']} E{episode['episode_number']}"
        )
    return {
        "episode_id": episode["title_id"],
        "episode_name": episode["display_title"],
        "season_number": episode["season_number"],
        "episode_number": episode["episode_number"],
        "still_path": episode.get("still"),
        "image": episode.get("still") or backdrop_path,
        "runtime_label": episode.get("duration_label") or "",
        "air_date": str(episode.get("start_year") or ""),
        "overview": episode.get("overview") or "暂无本集简介。",
        "imdb_rating": episode.get("average_rating"),
        "sources": sources,
        "featured_source": featured_source,
        "play_url": featured_source.get("play_url") if featured_source else None,
        "play_label": play_label,
        "progress": progress.get("percent") if has_progress else None,
        "position_label": progress.get("position_label") if has_progress else None,
        "is_completed": bool(progress.get("completed")),
        "source_summary": _source_summary(featured_source),
        "media_facts": _media_facts(featured_source),
    }


def _episode_panel_data(seasons: list[dict], genres: list[str]) -> dict | None:
    episodes_data = {}
    for season in seasons:
        for episode in season["episodes"]:
            featured = episode.get("featured_source")
            sources = [
                {
                    "label": source["source_label"],
                    "url": source["play_url"],
                    "path": source.get("alist_path") or "",
                    "active": bool(
                        featured and source["source_id"] == featured["source_id"]
                    ),
                }
                for source in episode["sources"]
            ]
            episodes_data[episode["episode_id"]] = {
                "season": episode["season_number"],
                "number": episode["episode_number"],
                "name": episode["episode_name"] or "未命名单集",
                "runtime": episode["runtime_label"],
                "air_date": episode["air_date"],
                "overview": episode["overview"],
                "rating": episode["imdb_rating"],
                "backdrop": (
                    url_for("static", filename=episode["image"])
                    if episode["image"]
                    else ""
                ),
                "play_label": episode["play_label"],
                "play_url": episode["play_url"],
                "sources": sources,
                "summary": episode["source_summary"],
                "facts": episode["media_facts"],
            }
    if not episodes_data:
        return None
    return {
        "genres": " / ".join(genres),
        "assets": {
            "imdb": url_for("static", filename="imdb.png"),
            "dolby_dark": url_for("static", filename="DolbyVision_white.png"),
            "dolby_light": url_for("static", filename="DolbyVision.png"),
        },
        "episodes": episodes_data,
    }


def _legacy_seasons(seasons: list[dict], backdrop_path: str | None) -> list[dict]:
    output = []
    for season in seasons:
        episodes = [
            _legacy_episode(episode, backdrop_path)
            for episode in season["episodes"]
        ]
        ratings = [
            episode["imdb_rating"]
            for episode in episodes
            if episode.get("imdb_rating") is not None
        ]
        years = [episode["air_date"] for episode in episodes if episode["air_date"]]
        season_number = season["season_number"]
        output.append(
            {
                "season_id": f"s{season_number}",
                "season_number": season_number,
                "season_name": (
                    "特别篇" if season_number == 0 else f"第 {season_number} 季"
                ),
                "air_date": min(years) if years else "",
                "episode_count": len(episodes),
                "imdb_rating": (
                    sum(ratings) / len(ratings) if ratings else None
                ),
                "episodes": episodes,
                "active": season["active"],
            }
        )
    return output


def build_title_viewmodel(raw: dict) -> dict:
    title = raw["root"]
    artwork = _group_artwork(raw["artwork"])
    backdrop_path = _primary_artwork(artwork, "landscape_poster")
    is_series = title["title_type"] == "series"

    episodes = _group_episodes(raw["episode_rows"], title["title_id"])
    selected_episode = _select_episode(
        episodes, raw["season_number"], raw["episode_number"]
    )

    if is_series:
        sources = selected_episode["sources"] if selected_episode else []
        featured_source = (
            selected_episode["featured_source"] if selected_episode else None
        )
        favorite_title_id = (
            selected_episode["title_id"] if selected_episode else title["title_id"]
        )
        collection_type = "series"
    else:
        sources = [build_source(source) for source in raw["files"]]
        featured_source = _choose_source(sources)
        favorite_title_id = title["title_id"]
        collection_type = "movie"

    runtime_label = ""
    if featured_source:
        runtime_label = featured_source["duration_label"]
    if not runtime_label:
        runtime_minutes = (
            selected_episode.get("runtime_minutes")
            if selected_episode
            else title.get("runtime_minutes")
        )
        if runtime_minutes:
            runtime_label = f"{runtime_minutes} 分钟"

    seasons = _legacy_seasons(
        _build_seasons(episodes, selected_episode), backdrop_path
    )
    selected_episode_id = selected_episode["title_id"] if selected_episode else None
    featured_episode = next(
        (
            episode
            for season in seasons
            for episode in season["episodes"]
            if episode["episode_id"] == selected_episode_id
        ),
        None,
    )
    current_season = next(
        (season for season in seasons if season["active"]),
        seasons[0] if seasons else None,
    )
    legacy_sources = [
        _legacy_source(source, index)
        for index, source in enumerate(sources, start=1)
    ]
    selected_file_id = featured_source["file_id"] if featured_source else None
    legacy_featured_source = next(
        (
            source
            for source in legacy_sources
            if source["file_id"] == selected_file_id
        ),
        legacy_sources[0] if legacy_sources else None,
    )
    source_summary = _source_summary(legacy_featured_source)
    source_progress = (
        legacy_featured_source.get("progress") if legacy_featured_source else {}
    ) or {}
    favorite = _favorite_state(
            raw,
            title_id=favorite_title_id,
            collection_title_type=collection_type,
        )
    external_links = [
        {
            "label": "IMDb",
            "url": f"https://www.imdb.com/title/{title['title_id']}/",
        }
    ]
    if title.get("tmdb_id"):
        external_links.append(
            {
                "label": "TMDB",
                "url": (
                    f"https://www.themoviedb.org/"
                    f"{'tv' if is_series else 'movie'}/{title['tmdb_id']}"
                ),
            }
        )

    media = {
        "media_id": title["title_id"],
        "media_type": "tv" if is_series else "movie",
        "name": title["display_title"],
        "original_name": title.get("original_title") or title.get("primary_title"),
        "logo_path": _primary_artwork(artwork, "logo"),
        "poster_path": _primary_artwork(artwork, "poster"),
        "backdrop_path": backdrop_path,
        "hero_image": (
            featured_episode["still_path"] if featured_episode else None
        )
        or backdrop_path,
        "tagline": title.get("tagline"),
        "overview": title.get("overview") or "暂无简介。",
        "genres": [
            genre.strip()
            for genre in (title.get("genres") or "").split(",")
            if genre.strip()
        ],
        "imdb_rating": title.get("average_rating"),
        "runtime_label": runtime_label,
        "date_label": format_year(title),
        "release_date": str(title.get("start_year") or ""),
        "last_air_date": str(title.get("end_year") or ""),
        "age_rating": None,
        "creators": [],
        "networks": [],
        "sources": legacy_sources,
        "featured_source": legacy_featured_source,
        "play_url": (
            legacy_featured_source.get("play_url")
            if legacy_featured_source
            else None
        ),
        "progress": source_progress.get("percent")
        if source_progress.get("position_ms")
        else None,
        "position_label": source_progress.get("position_label")
        if source_progress.get("position_ms")
        else None,
        "resolution": source_summary["resolution_label"],
        "video_codec_label": source_summary["video_codec_label"],
        "dynamic_range_label": source_summary["dynamic_range_label"],
        "is_dolby_vision": source_summary["is_dolby_vision"],
        "media_facts": _media_facts(legacy_featured_source),
        "seasons": seasons,
        "current_season": current_season,
        "featured_episode": featured_episode,
        "favorite": favorite,
        "is_favorite": bool(favorite and favorite["active"]),
        "external_links": external_links,
    }
    if not is_series:
        media["play_label"] = (
            f"继续播放 {source_progress['position_label']}"
            if source_progress.get("position_ms")
            else "播放电影"
        )
    else:
        media["episode_data"] = _episode_panel_data(seasons, media["genres"])
    return media
