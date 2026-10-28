from __future__ import annotations

from collections import defaultdict

from flask import url_for

from viewmodels.cards import (
    build_source,
    format_duration_ms,
    format_year,
)
from viewmodels.source_comparison import build_media_versions, build_source_options


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


def _resume_episode(episodes: list[dict]) -> tuple[dict | None, bool]:
    watched = [
        episode
        for episode in episodes
        if (episode.get("featured_source") or {}).get("watched_at")
    ]
    if not watched:
        return None, False

    latest = max(
        watched,
        key=lambda episode: (
            episode["featured_source"].get("watched_at") or "",
            episode["featured_source"].get("playback_id") or 0,
        ),
    )
    if not latest["featured_source"]["progress"]["completed"]:
        return latest, False

    latest_index = episodes.index(latest)
    next_episode = next(
        (
            episode
            for episode in episodes[latest_index + 1 :]
            if episode.get("featured_source")
        ),
        None,
    )
    return (next_episode or latest), next_episode is not None


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
    return_to: str,
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
        "return_to": return_to,
    }


def _build_episode_viewmodel(
    episode: dict,
    backdrop_path: str | None,
    selected_episode_id: str | None,
) -> dict:
    sources = build_source_options(episode["sources"])
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
        "badges": featured_source.get("badges", []) if featured_source else [],
        "versions": build_media_versions(sources, featured_source),
        "active": episode["title_id"] == selected_episode_id,
    }


def _build_season_viewmodels(
    seasons: list[dict],
    backdrop_path: str | None,
    selected_episode_id: str | None,
) -> list[dict]:
    output = []
    for season in seasons:
        episodes = [
            _build_episode_viewmodel(
                episode,
                backdrop_path,
                selected_episode_id,
            )
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


def _runtime_label(
    title: dict,
    selected_episode: dict | None,
    featured_source: dict | None,
) -> str:
    if featured_source and featured_source.get("duration_label"):
        return featured_source["duration_label"]
    runtime_minutes = (
        selected_episode.get("runtime_minutes")
        if selected_episode
        else title.get("runtime_minutes")
    )
    return f"{runtime_minutes} 分钟" if runtime_minutes else ""


def _find_episode_view(seasons: list[dict], episode_id: str | None) -> dict | None:
    return next(
        (
            episode
            for season in seasons
            for episode in season["episodes"]
            if episode["episode_id"] == episode_id
        ),
        None,
    )


def _build_episode_state(
    episodes: list[dict],
    selected_episode: dict | None,
    resume_episode: dict | None,
    resume_advanced: bool,
    backdrop_path: str | None,
) -> dict:
    selected_id = selected_episode["title_id"] if selected_episode else None
    seasons = _build_season_viewmodels(
        _build_seasons(episodes, selected_episode),
        backdrop_path,
        selected_id,
    )
    selected_view = _find_episode_view(seasons, selected_id)
    resume_id = resume_episode["title_id"] if resume_episode else None
    resume_view = _find_episode_view(seasons, resume_id)
    if resume_view and resume_advanced:
        resume_view["play_label"] = (
            f"播放 S{resume_view['season_number']} "
            f"E{resume_view['episode_number']}"
        )
    elif resume_view and resume_view.get("position_label"):
        resume_view["play_label"] = (
            f"继续播放 {resume_view['position_label']}"
        )
    return {
        "seasons": seasons,
        "selected": selected_view,
        "resume": resume_view,
        "current_season": next(
            (season for season in seasons if season["active"]),
            seasons[0] if seasons else None,
        ),
    }


def douban_url(douban_id) -> str | None:
    return f"https://movie.douban.com/subject/{douban_id}/" if douban_id and str(douban_id).isdigit() else None


def format_ratings(title: dict) -> list[dict]:
    ratings = []

    def add(key, name, field, icon, score_format, tooltip, votes_field=None, votes_format="", *, number=int, url=None):
        value = title.get(field)
        if value is None or value == "":
            return
        value = number(value)
        votes = title.get(votes_field) if votes_field else None
        ratings.append({
            "key": key,
            "name": name,
            "icon": icon(value) if callable(icon) else icon,
            "score": score_format.format(value),
            "title": tooltip.format(value) + (votes_format.format(votes) if votes else ""),
            "url": url,
        })

    add("imdb", "IMDb", "average_rating", "imdb.png", "{:.1f}",
        "IMDb: {:.1f}/10", "num_votes", " ({:,} 票)", number=float)
    add("douban", "豆瓣", "douban_rating", "icons/ratings/douban.svg", "{:.1f}",
        "豆瓣: {:.1f}/10", "douban_votes", " ({:,} 人评价)", number=float,
        url=douban_url(title.get("douban_id")))
    add("rt_critics", "Rotten Tomatoes (影评人)", "rt_critics_score",
        lambda score: "icons/ratings/" + (
            "rt_certified.svg" if score >= 75 else "rt_fresh.svg" if score >= 60 else "rt_rotten.svg"
        ), "{}%", "烂番茄影评人: {}%", "rt_critics_votes", " ({} 篇影评)")
    add("rt_audience", "Rotten Tomatoes (观众)", "rt_audience_score",
        lambda score: "icons/ratings/" + ("rt_popcorn.svg" if score >= 60 else "rt_spilled.svg"),
        "{}%", "烂番茄观众: {}%", "rt_audience_votes", " ({:,} 评分)")
    add("metacritic", "Metacritic", "metacritic_score",
        lambda score: "icons/ratings/" + (
            "metascore_positive.svg" if score >= 61 else "metascore_mixed.svg" if score >= 40 else "metascore_negative.svg"
        ), "{}", "Metacritic: {}/100", "metacritic_votes", " ({} 家媒体)")
    add("letterboxd", "Letterboxd", "letterboxd_score", "icons/ratings/letterboxd.svg",
        "{:.1f}", "Letterboxd: {:.1f}/5.0", "letterboxd_votes", " ({:,} 评分)", number=float)
    add("trakt", "Trakt", "trakt_score", "icons/ratings/trakt_colored.svg",
        "{}%", "Trakt: {}%", "trakt_votes", " ({:,} 评分)")
    add("rogerebert", "Roger Ebert", "roger_ebert_rating", "icons/ratings/star.svg",
        "{:.1f}", "Roger Ebert: {:.1f}/4.0", number=float)
    return ratings



def _build_source_state(
    sources: list[dict],
    featured_source: dict | None,
) -> dict:
    source_views = build_source_options(sources)
    selected_file_id = featured_source["file_id"] if featured_source else None
    featured_view = next(
        (
            source
            for source in source_views
            if source["file_id"] == selected_file_id
        ),
        source_views[0] if source_views else None,
    )
    return {
        "sources": source_views,
        "featured": featured_view,
        "progress": (featured_view.get("progress") if featured_view else {}) or {},
    }


def _external_links(title: dict, is_series: bool) -> list[dict]:
    links = [
        {
            "label": "IMDb",
            "url": f"https://www.imdb.com/title/{title['title_id']}/",
        }
    ]
    if title.get("tmdb_id"):
        links.append(
            {
                "label": "TMDB",
                "url": (
                    f"https://www.themoviedb.org/"
                    f"{'tv' if is_series else 'movie'}/{title['tmdb_id']}"
                ),
            }
        )
    if douban_url(title.get("douban_id")):
        links.append({"label": "豆瓣", "url": douban_url(title.get("douban_id"))})
    return links


def build_title_viewmodel(
    raw: dict,
    *,
    return_to: str = "/",
    select_resume: bool = False,
) -> dict:
    title = raw["root"]
    artwork = _group_artwork(raw["artwork"])
    backdrop_path = _primary_artwork(artwork, "landscape_poster")
    is_series = title["title_type"] == "series"

    episodes = _group_episodes(raw["episode_rows"], title["title_id"])
    requested_episode = _select_episode(
        episodes, raw["season_number"], raw["episode_number"]
    )
    resume_episode, resume_advanced = _resume_episode(episodes)
    explicit_episode = (
        raw["season_number"] is not None or raw["episode_number"] is not None
    )
    # 历史记录规则：未指定单集进入时按续播定位（看完≥90%或剩≤2分钟则进下一集），
    # 仅在显式指定单集且未强制续播时才用请求的单集。
    selected_episode = (
        resume_episode
        if resume_episode and (select_resume or not explicit_episode)
        else requested_episode
    )

    sources = (
        selected_episode["sources"]
        if is_series and selected_episode
        else [build_source(source) for source in raw["files"]]
    )
    featured_source = (
        selected_episode["featured_source"]
        if is_series and selected_episode
        else _choose_source(sources)
    )
    episode_state = _build_episode_state(
        episodes,
        selected_episode,
        resume_episode,
        resume_advanced,
        backdrop_path,
    )
    source_state = _build_source_state(sources, featured_source)
    featured_source_view = source_state["featured"]
    source_progress = source_state["progress"]
    favorite = _favorite_state(
        raw,
        title_id=title["title_id"],
        collection_title_type="series" if is_series else "movie",
        return_to=return_to,
    )
    genres = [
        genre.strip()
        for genre in (title.get("genres") or "").split(",")
        if genre.strip()
    ]
    playback_episode = episode_state["selected"]

    media = {
        "media_id": title["title_id"],
        "media_type": "tv" if is_series else "movie",
        "name": title["display_title"],
        "original_name": title.get("original_title") or title.get("primary_title"),
        "logo_path": _primary_artwork(artwork, "logo"),
        "poster_path": _primary_artwork(artwork, "poster"),
        "backdrop_path": backdrop_path,
        "hero_image": backdrop_path
        or (
            episode_state["selected"]["still_path"]
            if episode_state["selected"]
            else None
        ),
        "tagline": title.get("tagline"),
        "overview": title.get("overview") or "暂无简介。",
        "genres": genres,
        "imdb_rating": title.get("average_rating"),
        "ratings": format_ratings(title),
        "runtime_label": _runtime_label(title, selected_episode, featured_source),
        "date_label": format_year(title),
        "release_date": str(title.get("start_year") or ""),
        "last_air_date": str(title.get("end_year") or ""),
        "age_rating": None,
        "creators": [],
        "networks": [],
        "cast": [
            {
                "person_id": person["tmdb_person_id"],
                "name": person["name"],
                "character": person.get("character_name"),
                "image": person.get("local_path"),
            }
            for person in raw["cast"]
        ],
        "sources": source_state["sources"],
        "featured_source": featured_source_view,
        "play_url": (
            featured_source_view.get("play_url")
            if featured_source_view
            else None
        ),
        "progress": source_progress.get("percent")
        if source_progress.get("position_ms")
        else None,
        "position_label": source_progress.get("position_label")
        if source_progress.get("position_ms")
        else None,
        "badges": (
            featured_source_view.get("badges", [])
            if featured_source_view
            else []
        ),
        "versions": build_media_versions(source_state["sources"], featured_source_view),
        "seasons": episode_state["seasons"],
        "current_season": episode_state["current_season"],
        "selected_episode": episode_state["selected"],
        "resume_episode": episode_state["resume"],
        "playback_episode": playback_episode,
        "favorite": favorite,
        "is_favorite": bool(favorite and favorite["active"]),
        "external_links": _external_links(title, is_series),
    }
    if not is_series:
        media["play_label"] = (
            f"继续播放 {source_progress['position_label']}"
            if source_progress.get("position_ms")
            and not source_progress.get("completed")
            else "播放"
        )
    return media
