"""TMDB metadata and artwork connector."""

import logging
import os
import re
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Lock, local
from time import monotonic

import requests

from connectors import throttle

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TMDB_API = os.environ.get("TMDB_API_ORIGIN", "https://api.themoviedb.org/3").rstrip("/")
TMDB_IMAGE = os.environ.get("TMDB_IMAGE_ORIGIN", "https://image.tmdb.org/t/p").rstrip("/")
LANGUAGE = "zh-CN"
IMAGE_LANGUAGE = "zh,null"
REQUEST_TIMEOUT = 20
ARTWORK_LIMIT = 5
CAST_LIMIT = 24
DOWNLOAD_ATTEMPTS = 3
IMAGE_WORKERS = 4
# append_to_response 官方上限为 20 项。
APPEND_LIMIT = 20
TITLE_ID_PATTERN = re.compile(r"tt[0-9]+")
# 中文名按地区回退，与原 IMDb 别名的 CN → TW → HK 顺序一致。
CHINESE_REGIONS = ("CN", "TW", "HK", "SG")
# TMDB 分类 ID → 库内沿用的 IMDb 英文分类；TV Movie 等无对应项的分类不写入。
GENRE_NAMES = {
    28: ("Action",), 12: ("Adventure",), 16: ("Animation",), 35: ("Comedy",),
    80: ("Crime",), 99: ("Documentary",), 18: ("Drama",), 10751: ("Family",),
    14: ("Fantasy",), 36: ("History",), 27: ("Horror",), 10402: ("Music",),
    9648: ("Mystery",), 10749: ("Romance",), 878: ("Sci-Fi",), 53: ("Thriller",),
    10752: ("War",), 37: ("Western",),
    10759: ("Action", "Adventure"), 10762: ("Family",), 10763: ("News",),
    10764: ("Reality-TV",), 10765: ("Sci-Fi", "Fantasy"), 10766: ("Drama",),
    10767: ("Talk-Show",), 10768: ("War",),
}
# 与 library.db 现存数据约定一致：poster 存 w500，backdrop/logo 存 original
POSTER_SIZE = "w500"
PROFILE_SIZE = "w342"
# local_path 形如 images/tt0816692/posters/1.jpg，去掉该前缀即相对 image_root
LOCAL_PATH_PREFIX = "images"
DEFAULT_IMAGE_ROOT = PROJECT_ROOT / "static" / "images"

logger = logging.getLogger(__name__)

# 短时、有界缓存；同一键的并发请求共用一个 Future，失败不缓存。
CACHE_TTL = 600
CACHE_LIMIT = 128
_cache = OrderedDict()
_pending = {}
_cache_lock = Lock()


def _remember(key, value):
    with _cache_lock:
        _cache[key] = (monotonic() + CACHE_TTL, value)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)


def _cached(key, fetch):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > monotonic():
            _cache.move_to_end(key)
            return entry[1]
        future = _pending.get(key)
        owner = future is None
        if owner:
            future = _pending[key] = Future()
    if not owner:
        return future.result()
    try:
        value = fetch()
        _remember(key, value)
        future.set_result(value)
        return value
    except Exception as error:
        future.set_exception(error)
        raise
    finally:
        with _cache_lock:
            _pending.pop(key, None)


def search_media(query: str, media_type: str, page: int = 1) -> dict:
    def fetch():
        with _session() as session:
            return _get(session, f"/search/{media_type}", {
                "query": query, "page": page, "language": LANGUAGE,
                "include_adult": "false",
            })
    return _cached(("search", media_type, query, LANGUAGE, page), fetch)


def fetch_title_summary(media_type: str, tmdb_id: int) -> dict:
    """搜索结果选中后取 IMDb ID、中文名和年份（一次详情请求）。"""
    def fetch():
        with _session() as session:
            return _get(session, f"/{media_type}/{tmdb_id}", {
                "language": LANGUAGE, "append_to_response": "external_ids",
            })
    detail = _cached(("summary", media_type, tmdb_id), fetch)
    date = detail.get("release_date" if media_type == "movie" else "first_air_date")
    return {
        "title_id": (detail.get("external_ids") or {}).get("imdb_id") or "",
        "title_type": "movie" if media_type == "movie" else "series",
        "display_title": detail.get("title" if media_type == "movie" else "name") or "",
        "start_year": _year(date),
    }


def chinese_search_poster(media_type: str, tmdb_id: int) -> str | None:
    def fetch():
        with _session() as session:
            payload = _get(session, f"/{media_type}/{tmdb_id}/images", {"include_image_language": "zh,null,en"})
        posters = payload.get("posters") or []
        posters.sort(key=lambda image: (image.get("iso_639_1") != "zh", -(image.get("vote_average") or 0)))
        return posters[0]["file_path"] if posters else None
    return _cached(("search_poster", media_type, tmdb_id), fetch)


def remember_media_identity(imdb_id: str, media_type: str, tmdb_id: int):
    """复用已确认的 IMDb → TMDB 映射，避免导入时重复 find。"""
    _remember(("find", imdb_id, LANGUAGE), (media_type, {"id": tmdb_id}))


def _token() -> str:
    """读取 TMDB_TOKEN：优先环境变量，回退项目根目录 .env。"""
    token = os.environ.get("TMDB_TOKEN", "").strip()
    if token:
        return token

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TMDB_TOKEN="):
                token = line.split("=", 1)[1].strip()
                if token:
                    return token

    raise RuntimeError("未配置 TMDB_TOKEN（环境变量或项目根目录 .env）。")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {"accept": "application/json", "Authorization": f"Bearer {_token()}"}
    )
    return session


def _get(session: requests.Session, endpoint: str, params: dict) -> dict:
    response = throttle.call(
        "tmdb",
        lambda: session.get(TMDB_API + endpoint, params=params, timeout=REQUEST_TIMEOUT),
    )
    response.raise_for_status()
    return response.json()




# ---------------------------------------------------------------- TMDB 请求


def find_media(session: requests.Session, imdb_id: str) -> tuple[str, dict]:
    """用 IMDb ID 换取媒体类型（movie/tv/episode）与 find 结果首条。"""
    return _cached(("find", imdb_id, LANGUAGE), lambda: _find_media(session, imdb_id))


def _find_media(session: requests.Session, imdb_id: str) -> tuple[str, dict]:
    payload = _get(
        session,
        f"/find/{imdb_id}",
        {"external_source": "imdb_id", "language": LANGUAGE},
    )

    for media_type, key in (
        ("movie", "movie_results"),
        ("tv", "tv_results"),
        ("episode", "tv_episode_results"),
    ):
        results = payload.get(key) or []
        if results:
            return media_type, results[0]

    raise ValueError(f"TMDB 中未找到 {imdb_id} 对应的电影、剧集或单集。")



def fetch_detail(
    session: requests.Session,
    media_type: str,
    tmdb_id: int,
    append: str = "",
) -> dict:
    """请求详情端点，可选挂载 append_to_response 子资源。"""
    params = {
        "language": LANGUAGE,
        "include_image_language": IMAGE_LANGUAGE,
        "append_to_response": append,
    }
    return _get(session, f"/{media_type}/{tmdb_id}", params)


def _append_chunks(session: requests.Session, tmdb_id: int, keys: list[str]) -> dict:
    """按 20 项一组挂载剧集子资源，合并返回 {key: payload}。"""
    merged = {}
    for start in range(0, len(keys), APPEND_LIMIT):
        chunk = keys[start:start + APPEND_LIMIT]
        detail = fetch_detail(session, "tv", tmdb_id, append=",".join(chunk))
        merged.update({key: detail[key] for key in chunk if detail.get(key)})
    return merged


def fetch_seasons(
    session: requests.Session, tmdb_id: int, seasons: list[dict]
) -> list[dict]:
    """挂载所有有单集的季，返回季 payload 列表。"""
    keys = [
        f"season/{int(season['season_number'])}"
        for season in seasons
        if season.get("episode_count", 0) > 0 and season.get("season_number") is not None
    ]
    payloads = _append_chunks(session, tmdb_id, keys)
    return [payloads[key] for key in keys if key in payloads]


def fetch_episode_ids(
    session: requests.Session, tmdb_id: int, positions: list[tuple[int, int]]
) -> dict[tuple[int, int], str]:
    """批量取单集 IMDb ID；没有 IMDb ID 的单集不返回。"""
    keys = {f"season/{s}/episode/{e}/external_ids": (s, e) for s, e in positions}
    payloads = _append_chunks(session, tmdb_id, list(keys))
    ids = {}
    for key, position in keys.items():
        imdb_id = (payloads.get(key) or {}).get("imdb_id") or ""
        if TITLE_ID_PATTERN.fullmatch(imdb_id):
            ids[position] = imdb_id
    return ids


# ---------------------------------------------------------------- 纯转换


def _suffix(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".svg"} else ".jpg"


def _year(value) -> int | None:
    value = str(value or "")[:4]
    return int(value) if value.isdigit() else None


def _genres(details: dict) -> str | None:
    names = [
        name
        for genre in details.get("genres") or []
        for name in GENRE_NAMES.get(genre.get("id"), ())
    ]
    return ",".join(dict.fromkeys(names)) or None


def _translations(details: dict, key: str) -> dict[tuple, str]:
    names = {}
    for item in (details.get("translations") or {}).get("translations") or []:
        value = ((item.get("data") or {}).get(key) or "").strip()
        if value:
            names.setdefault((item.get("iso_639_1"), item.get("iso_3166_1")), value)
    return names


def build_title_basic(details: dict, title_id: str, title_type: str) -> dict:
    """组装电影或剧集的 title_basic 行；IMDb 评分由调用方另行填入。"""
    key = "title" if title_type == "movie" else "name"
    original = details.get(f"original_{key}") or None
    names = _translations(details, key)
    chinese = next((names[("zh", region)] for region in CHINESE_REGIONS if ("zh", region) in names), None)
    english = names.get(("en", "US")) or next(
        (value for (language, _), value in names.items() if language == "en"), None
    )
    if title_type == "movie":
        start_year = _year(details.get("release_date"))
        end_year = None
        runtime = details.get("runtime") or None
    else:
        start_year = _year(details.get("first_air_date"))
        # last_air_date 不是停播年份，只在已完结时写入。
        ended = details.get("status") in ("Ended", "Canceled")
        end_year = _year(details.get("last_air_date")) if ended else None
        runtime = next(iter(details.get("episode_run_time") or []), None) or None
    return {
        "title_id": title_id,
        "tmdb_id": int(details["id"]),
        "title_type": title_type,
        "primary_title": english or original or details.get(key),
        "original_title": original,
        "original_language": details.get("original_language"),
        "display_title": chinese or details.get(key) or original or title_id,
        "is_adult": int(bool(details.get("adult"))),
        "start_year": start_year,
        "end_year": end_year,
        "runtime_minutes": runtime,
        "genres": _genres(details),
        "overview": details.get("overview") or None,
        "tagline": details.get("tagline") or None,
        "average_rating": None,
        "num_votes": None,
    }


def _pick_images(images: list[dict], languages: tuple, *, fallback: bool = False) -> list[dict]:
    """按语言过滤图片，可选在无命中时回退到全部。"""
    matched = [
        image for image in images if image.get("file_path") and image.get("iso_639_1") in languages
    ]
    if not matched and fallback:
        matched = [image for image in images if image.get("file_path")]
    return matched


def _artwork_rows(
    title_id: str,
    artwork_type: str,
    image_key: str,
    picked: list[dict],
    local_prefix: str,
    size: str = "original",
) -> list[dict]:
    """把一类图片转成 title_artwork 行，最多 ARTWORK_LIMIT 条。"""
    rows = []
    for position, image in enumerate(picked[:ARTWORK_LIMIT], 1):
        file_path = image["file_path"]
        rows.append(
            {
                "title_id": title_id,
                "artwork_type": artwork_type,
                "position": position,
                "remote_path": f"{TMDB_IMAGE}/{size}/{file_path.lstrip('/')}",
                "local_path": f"{local_prefix}/{image_key}/{position}{_suffix(file_path)}",
            }
        )
    return rows


def build_title_artwork(imdb_id: str, images: dict) -> list[dict]:
    """中文海报 ×5、纯图 backdrop ×5、中文 logo ×5。目录以 Title ID 命名。"""
    posters = _pick_images(images.get("posters") or [], ("zh",), fallback=True)
    backdrops = _pick_images(images.get("backdrops") or [], (None,))
    logos = _pick_images(images.get("logos") or [], ("zh",), fallback=True)

    local_prefix = f"{LOCAL_PATH_PREFIX}/{imdb_id}"

    return (
        _artwork_rows(imdb_id, "poster", "posters", posters, local_prefix, POSTER_SIZE)
        + _artwork_rows(imdb_id, "landscape_poster", "backdrops", backdrops, local_prefix)
        + _artwork_rows(imdb_id, "logo", "logos", logos, local_prefix)
    )


def build_title_cast(title_id: str, cast: list[dict]) -> list[dict]:
    """组装详情页使用的前 24 位演员和单张人物图。"""
    rows = []
    seen = set()
    for person in cast:
        person_id = person.get("id")
        name = (person.get("name") or "").strip()
        if not person_id or not name or person_id in seen:
            continue
        seen.add(person_id)
        profile_path = person.get("profile_path")
        characters = list(
            dict.fromkeys(
                role["character"].strip()
                for role in person.get("roles") or []
                if role.get("character") and role["character"].strip()
            )
        )
        rows.append(
            {
                "title_id": title_id,
                "tmdb_person_id": int(person_id),
                "name": name,
                "character_name": (
                    " / ".join(characters)
                    or (person.get("character") or "").strip()
                    or None
                ),
                "cast_order": int(person.get("order", len(rows))),
                "remote_path": (
                    f"{TMDB_IMAGE}/{PROFILE_SIZE}/{profile_path.lstrip('/')}"
                    if profile_path
                    else None
                ),
                "local_path": (
                    f"{LOCAL_PATH_PREFIX}/people/{person_id}{_suffix(profile_path)}"
                    if profile_path
                    else None
                ),
            }
        )
        if len(rows) == CAST_LIMIT:
            break
    return rows


def fetch_title_cast(
    title_id: str,
    media_type: str,
    tmdb_id: int,
    session: requests.Session | None = None,
) -> list[dict]:
    own_session = session is None
    session = session or _session()
    try:
        credits_key = "aggregate_credits" if media_type == "tv" else "credits"
        detail = fetch_detail(session, media_type, tmdb_id, append=credits_key)
        return build_title_cast(
            title_id,
            (detail.get(credits_key) or {}).get("cast") or [],
        )
    finally:
        if own_session:
            session.close()



def build_episode(episode: dict, series: dict, episode_title_id: str) -> dict:
    """组装单集的 title_basic 行与 still；series 为父剧集的 title_basic 行。

    still 的 local_path 为 images/{series_title_id}/{episode_title_id}-still{ext}。
    """
    season_number = episode.get("season_number")
    episode_number = episode.get("episode_number")
    still_path = episode.get("still_path")
    name = (episode.get("name") or "").strip() or f"第 {episode_number} 集"
    artwork = []
    if still_path:
        artwork.append(
            {
                "title_id": episode_title_id,
                "artwork_type": "still",
                "position": 1,
                "remote_path": still_path,
                "local_path": (
                    f"{LOCAL_PATH_PREFIX}/{series['title_id']}"
                    f"/{episode_title_id}-still{_suffix(still_path)}"
                ),
            }
        )
    return {
        "season_number": season_number,
        "episode_number": episode_number,
        "title_basic": {
            "title_id": episode_title_id,
            "tmdb_id": int(episode["id"]),
            "title_type": "episode",
            "primary_title": name,
            "original_title": None,
            "original_language": series.get("original_language"),
            "display_title": name,
            "is_adult": series.get("is_adult", 0),
            "start_year": _year(episode.get("air_date")),
            "end_year": None,
            "runtime_minutes": episode.get("runtime") or None,
            "genres": series.get("genres"),
            "overview": episode.get("overview") or None,
            "tagline": None,
            "average_rating": None,
            "num_votes": None,
        },
        "title_artwork": artwork,
    }


# ---------------------------------------------------------------- 组装入口


def identify_title(imdb_id: str, session: requests.Session | None = None) -> dict:
    """IMDb ID → 类型与根作品 ID；单集解析出父剧集 IMDb ID 和季集号。"""
    own_session = session is None
    session = session or _session()
    try:
        media_type, found = find_media(session, imdb_id)
        if media_type == "movie":
            return {"title_type": "movie", "root_id": imdb_id}
        if media_type == "tv":
            return {"title_type": "series", "root_id": imdb_id}
        show_id = int(found["show_id"])
        series = _cached(
            ("external_ids", "tv", show_id),
            lambda: _get(session, f"/tv/{show_id}/external_ids", {}),
        )
        root_id = series.get("imdb_id") or ""
        if not TITLE_ID_PATTERN.fullmatch(root_id):
            raise ValueError(f"TMDB 中 {imdb_id} 的父剧集没有 IMDb ID。")
        remember_media_identity(root_id, "tv", show_id)
        return {
            "title_type": "episode",
            "root_id": root_id,
            "season_number": found.get("season_number"),
            "episode_number": found.get("episode_number"),
        }
    finally:
        if own_session:
            session.close()


def _series_episodes(
    session: requests.Session,
    tmdb_id: int,
    series: dict,
    seasons: list[dict],
    known_episode_ids: dict[tuple[int, int], str],
) -> list[dict]:
    """组装有 IMDb ID 的单集；库内已有的季集关系优先，冲突的 ID 跳过并告警。"""
    episodes = {
        (episode.get("season_number"), episode.get("episode_number")): episode
        for season in seasons
        for episode in season.get("episodes") or []
        if episode.get("season_number") is not None and episode.get("episode_number") is not None
    }
    ids = {position: known_episode_ids[position] for position in episodes if position in known_episode_ids}
    missing = [position for position in episodes if position not in ids]
    used = set(known_episode_ids.values())
    for position, imdb_id in fetch_episode_ids(session, tmdb_id, missing).items():
        if imdb_id in used:
            logger.warning("单集 %s 已对应其他季集，跳过 TMDB 的 S%sE%s", imdb_id, *position)
            continue
        used.add(imdb_id)
        ids[position] = imdb_id
    return [build_episode(episode, series, ids[position]) for position, episode in episodes.items() if position in ids]


def build_media_metadata(
    imdb_id: str,
    session: requests.Session | None = None,
    known_episode_ids: dict[tuple[int, int], str] | None = None,
) -> dict:
    """主入口：电影或剧集 IMDb ID → 与 library.db 对应的完整元数据（不含 IMDb 评分）。

    电影 2 次请求（find + detail 挂载 images/credits/translations）；
    剧集另加 ceil(季数/20) 次季详情，以及 ceil(新单集数/20) 次单集 IMDb ID。
    known_episode_ids 为库内已有的 {(季, 集): 单集 ID}，可省去这些单集的 ID 请求。
    结果缓存 10 分钟，返回副本。
    """
    imdb_id = imdb_id.strip()
    known = dict(known_episode_ids or {})
    key = ("metadata", imdb_id, LANGUAGE, tuple(sorted(known.items())))

    def fetch():
        own_session = session is None
        current = session or _session()
        try:
            return _build_media_metadata(current, imdb_id, known)
        finally:
            if own_session:
                current.close()

    return deepcopy(_cached(key, fetch))


def _build_media_metadata(session: requests.Session, imdb_id: str, known: dict) -> dict:
    media_type, found = find_media(session, imdb_id)
    if media_type == "episode":
        raise ValueError(f"{imdb_id} 是单集，请使用父剧集的 IMDb ID。")

    tmdb_id = int(found["id"])
    credits_key = "aggregate_credits" if media_type == "tv" else "credits"
    detail = fetch_detail(
        session,
        media_type,
        tmdb_id,
        append=f"images,{credits_key},translations",
    )
    title = build_title_basic(detail, imdb_id, "movie" if media_type == "movie" else "series")
    metadata = {
        "title_basic": title,
        "title_artwork": build_title_artwork(imdb_id, detail.get("images") or {}),
        "title_cast": build_title_cast(
            imdb_id,
            (detail.get(credits_key) or {}).get("cast") or [],
        ),
        "episodes": [],
    }
    if media_type == "tv":
        seasons = fetch_seasons(session, tmdb_id, detail.get("seasons") or [])
        metadata["episodes"] = _series_episodes(session, tmdb_id, title, seasons, known)
    return metadata


# ---------------------------------------------------------------- 图片下载


def _download_image(session: requests.Session, image_url: str, target_path: Path):
    """下载单张图片到临时文件后原子替换，带重试。"""
    temporary_path = target_path.with_suffix(target_path.suffix + ".part")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    last_error = None
    for _ in range(DOWNLOAD_ATTEMPTS):
        try:
            with throttle.call(
                "tmdb-image",
                lambda: session.get(image_url, timeout=REQUEST_TIMEOUT, stream=True),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    raise ValueError(f"返回的不是图片（{content_type}）")
                with temporary_path.open("wb") as image_file:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            image_file.write(chunk)
            temporary_path.replace(target_path)
            return
        except throttle.RateLimitError:
            temporary_path.unlink(missing_ok=True)
            raise
        except (requests.RequestException, ValueError) as error:
            temporary_path.unlink(missing_ok=True)
            last_error = error

    raise last_error


def _download_url(row: dict) -> str:
    """still 的 remote_path 是裸路径（补 original 尺寸），其余已是完整 URL。"""
    remote_path = row["remote_path"]
    if remote_path.startswith("http"):
        return remote_path
    return f"{TMDB_IMAGE}/original/{remote_path.lstrip('/')}"


def _target_path(image_root: Path, row: dict) -> Path:
    """local_path 去掉 images/ 前缀，映射到 image_root 下的实际位置。"""
    relative = Path(*Path(row["local_path"]).parts[1:])
    return image_root / relative


def download_metadata_images(
    metadata: dict,
    image_root: str | Path = DEFAULT_IMAGE_ROOT,
    session: requests.Session | None = None,
) -> dict:
    """批量下载 metadata 中全部图片（title_artwork、演员图与单集 still）。

    默认不启用；由调用方或 CLI --download 显式触发。
    已存在的文件跳过，单张失败重试后跳过，不中断整体。
    返回 {"downloaded": n, "skipped": n, "failed": n} 统计。
    """
    image_root = Path(image_root)
    rows = list(metadata.get("title_artwork") or [])
    rows.extend(
        person
        for person in metadata.get("title_cast") or []
        if person.get("remote_path") and person.get("local_path")
    )
    for episode in metadata.get("episodes") or []:
        rows.extend(episode.get("title_artwork") or [])

    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    stats_lock = Lock()
    pending, queued = [], set()
    for row in rows:
        target_path = _target_path(image_root, row)
        if target_path.exists() or target_path in queued:
            stats["skipped"] += 1
        else:
            queued.add(target_path)
            pending.append((row, target_path))

    # 图片来自 CDN，可适度并行；共享 session 时沿用调用方的，否则每个线程各自一个。
    own_sessions, sessions = [], local()

    def current_session() -> requests.Session:
        if session is not None:
            return session
        if not hasattr(sessions, "value"):
            sessions.value = _session()
            own_sessions.append(sessions.value)
        return sessions.value

    def download(job) -> None:
        row, target_path = job
        try:
            _download_image(current_session(), _download_url(row), target_path)
            outcome = "downloaded"
        except (requests.RequestException, ValueError) as error:
            logger.warning("下载失败 %s: %s", row["local_path"], error)
            outcome = "failed"
        with stats_lock:
            stats[outcome] += 1

    try:
        if len(pending) > 1:
            with ThreadPoolExecutor(max_workers=IMAGE_WORKERS, thread_name_prefix="tmdb-image") as pool:
                list(pool.map(download, pending))
        else:
            for job in pending:
                download(job)
    finally:
        for own in own_sessions:
            own.close()

    return stats
