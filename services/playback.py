COMPLETION_RATIO = 0.90
REMAINING_THRESHOLD_MS = 2 * 60 * 1000


def is_effectively_completed(
    position_ms: int | None,
    duration_ms: int | None,
) -> bool:
    position = max(int(position_ms or 0), 0)
    duration = max(int(duration_ms or 0), 0)
    if duration <= 0:
        return False
    remaining = max(duration - position, 0)
    return position / duration >= COMPLETION_RATIO or remaining < REMAINING_THRESHOLD_MS



def choose_source(sources: list[dict]) -> dict | None:
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


def select_episode(
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


def resume_episode(episodes: list[dict]) -> tuple[dict | None, bool]:
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
    source = latest["featured_source"]
    if not is_effectively_completed(source.get("position_ms"), source.get("duration_ms")):
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


def episode_order(episode: dict) -> tuple:
    return (
        episode["season_number"] if episode["season_number"] is not None else -1,
        episode["episode_number"] if episode["episode_number"] is not None else -1,
        episode["title_id"],
    )


def series_resume_source(rows: list[dict]) -> dict | None:
    """Choose a playback target from query rows without building page data."""
    episodes = {}
    for row in rows:
        episode = episodes.setdefault(row["episode_id"], {
            "title_id": row["episode_id"], "season_number": row["season_number"],
            "episode_number": row["episode_number"], "sources": [],
        })
        if row.get("file_id") is not None:
            episode["sources"].append(row)
    ordered = sorted(episodes.values(), key=episode_order)
    for episode in ordered:
        episode["featured_source"] = choose_source(episode["sources"])
    selected, _ = resume_episode(ordered)
    selected = selected or select_episode(ordered, None, None)
    return selected["featured_source"] if selected else None
