import re
from pathlib import PurePosixPath

COMPLETION_RATIO = 0.90
REMAINING_THRESHOLD_MS = 2 * 60 * 1000
SUBTITLE_EXTENSIONS = frozenset({".ass", ".ssa", ".srt", ".vtt", ".sup", ".sub"})
SUBTITLE_DIRS = frozenset({"sub", "subs", "subtitle", "subtitles", "字幕", "外挂字幕"})
MAX_SUBTITLES = 6
_EPISODE_RE = re.compile(r"(?:^|[^a-z0-9])s(\d{1,2})[._ -]?e(\d{1,3})(?:$|[^a-z0-9])", re.IGNORECASE)
_DELIMITERS = frozenset("._- []()【】")


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


def _episode_tag(stem: str) -> tuple[int, int] | None:
    match = _EPISODE_RE.search(stem)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _stem_matches(video_stem: str, sub_stem: str) -> bool:
    base = video_stem.casefold()
    candidate = sub_stem.casefold()
    if candidate == base:
        return True
    return candidate.startswith(base) and candidate[len(base)] in _DELIMITERS


def _subtitle_rank(video_stem: str, sub_path: str) -> tuple:
    name = PurePosixPath(sub_path).name
    stem = PurePosixPath(name).stem
    ext = PurePosixPath(name).suffix.lower()
    lower = stem.casefold()
    exact_prefix = 0 if _stem_matches(video_stem, stem) else 1
    if any(tag in lower for tag in ("简", "双语", "中英", "chs", "zh-cn", "zh_cn", "zh-hans", ".sc")):
        lang = 0
    elif any(tag in lower for tag in ("繁", "cht", "zh-tw", "zh_tw", "zh-hant", ".tc")):
        lang = 1
    elif any(tag in lower for tag in ("中", "chi", "zho", ".zh")):
        lang = 2
    elif any(tag in lower for tag in ("eng", ".en", "英")):
        lang = 3
    else:
        lang = 4
    ext_order = {".ass": 0, ".ssa": 1, ".srt": 2, ".vtt": 3, ".sup": 4, ".sub": 5}.get(ext, 9)
    return (lang, exact_prefix, ext_order, name.casefold())


def match_subtitles(
    video_path: str,
    parent_entries: list[dict],
    subdir_entries: list[tuple[str, list[dict]]] = (),
    *,
    video_extensions: frozenset[str] = frozenset(),
) -> list[str]:
    """在同目录与字幕子目录里挑出属于当前视频的外挂字幕路径（按简中/ASS 优先排序）。"""
    pure = PurePosixPath(video_path)
    parent = str(pure.parent)
    video_stem = pure.stem
    video_ep = _episode_tag(video_stem)
    videos = [
        item["name"] for item in parent_entries
        if not item.get("is_dir") and PurePosixPath(item["name"]).suffix.lower() in video_extensions
    ]
    # 独立电影文件夹（非网盘根目录且目录内只有这一部视频）：同目录及字幕子目录里的字幕全部归它
    single_video_dir = len(videos) == 1 and len(pure.parts) >= 4
    # 剧集目录：若目录内每集 SxxExx 唯一，允许不同压制组命名的同季集字幕匹配
    ep_counts: dict[tuple[int, int], int] = {}
    for vname in videos:
        tag = _episode_tag(PurePosixPath(vname).stem)
        if tag is not None:
            ep_counts[tag] = ep_counts.get(tag, 0) + 1
    unique_episode = video_ep is not None and ep_counts.get(video_ep) == 1

    candidates: list[str] = []
    sources = [(parent, parent_entries), *subdir_entries]
    for folder, entries in sources:
        for item in entries:
            if item.get("is_dir") or not item.get("name"):
                continue
            ext = PurePosixPath(item["name"]).suffix.lower()
            if ext not in SUBTITLE_EXTENSIONS:
                continue
            sub_stem = PurePosixPath(item["name"]).stem
            if (
                single_video_dir
                or _stem_matches(video_stem, sub_stem)
                or (unique_episode and _episode_tag(sub_stem) == video_ep)
            ):
                candidates.append(str(PurePosixPath(folder) / item["name"]))

    ranked = sorted(dict.fromkeys(candidates), key=lambda path: _subtitle_rank(video_stem, path))
    return ranked[:MAX_SUBTITLES]
