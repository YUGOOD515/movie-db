"""TMDB metadata and artwork connector."""

import logging
import os
import sqlite3
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMDB_DB = PROJECT_ROOT / "imdb.db"

TMDB_API = os.environ.get("TMDB_API_ORIGIN", "https://api.themoviedb.org/3").rstrip("/")
TMDB_IMAGE = "https://image.tmdb.org/t/p"
LANGUAGE = "zh-CN"
IMAGE_LANGUAGE = "zh,null"
REQUEST_TIMEOUT = 20
ARTWORK_LIMIT = 5
CAST_LIMIT = 24
DOWNLOAD_ATTEMPTS = 3
# 与 library.db 现存数据约定一致：poster 存 w500，backdrop/logo 存 original
POSTER_SIZE = "w500"
PROFILE_SIZE = "w342"
# local_path 形如 images/tt0816692/posters/1.jpg，去掉该前缀即相对 image_root
LOCAL_PATH_PREFIX = "images"
DEFAULT_IMAGE_ROOT = PROJECT_ROOT / "static" / "images"

logger = logging.getLogger(__name__)


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
    response = session.get(
        TMDB_API + endpoint, params=params, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()




# ---------------------------------------------------------------- TMDB 请求


def find_media(session: requests.Session, imdb_id: str) -> tuple[str, dict]:
    """用 IMDb ID 换取媒体类型（movie/tv/episode）与 find 结果首条。"""
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


def fetch_seasons(
    session: requests.Session, tmdb_id: int, seasons: list[dict]
) -> list[dict]:
    """一次请求挂载所有有单集的季，返回季 payload 列表。"""
    numbers = [
        int(season["season_number"])
        for season in seasons
        if season.get("episode_count", 0) > 0 and season.get("season_number") is not None
    ]
    if not numbers:
        return []

    append = ",".join(f"season/{n}" for n in numbers)
    detail = fetch_detail(session, "tv", tmdb_id, append=append)
    return [detail[f"season/{n}"] for n in numbers if detail.get(f"season/{n}")]



# ---------------------------------------------------------------- 纯转换


def _suffix(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".svg"} else ".jpg"


def build_title_supplement(details: dict) -> dict:
    """组装 title_basic 的 TMDB 补充字段（电影或剧集）。"""
    return {
        "tmdb_id": int(details["id"]),
        "overview": details.get("overview") or None,
        "tagline": details.get("tagline") or None,
        "original_language": details.get("original_language"),
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



def build_episode(
    episode: dict,
    series_title_id: str,
    series_language: str | None,
    episode_title_id: str | None = None,
) -> dict:
    """组装单个单集的 TMDB 补充数据 + still。

    season_number / episode_number / tmdb_id 为匹配键，供调用方对齐
    imdb.db 的 title_episode；不作为入库字段。
    still 的 local_path 为 images/{series_title_id}/{episode_title_id}-still{ext}；
    episode_title_id 未知时回退用 TMDB 单集 ID 命名并告警。
    """
    episode_tmdb_id = int(episode["id"])
    season_number = episode.get("season_number")
    still_path = episode.get("still_path")

    artwork = []
    if still_path:
        episode_identifier = episode_title_id
        if episode_identifier is None:
            logger.warning(
                "单集 %s (S%sE%s) 在本地 imdb.db 未找到 Title ID，剧照用 TMDB ID 命名",
                episode_tmdb_id, season_number, episode.get("episode_number"),
            )
            episode_identifier = str(episode_tmdb_id)
        artwork.append(
            {
                "title_id": episode_title_id,
                "artwork_type": "still",
                "position": 1,
                "remote_path": still_path,
                "local_path": (
                    f"{LOCAL_PATH_PREFIX}/{series_title_id}"
                    f"/{episode_identifier}-still{_suffix(still_path)}"
                ),
            }
        )

    return {
        "season_number": season_number,
        "episode_number": episode.get("episode_number"),
        "title_basic": {
            "tmdb_id": episode_tmdb_id,
            "overview": episode.get("overview") or None,
            "tagline": None,
            "original_language": series_language,
        },
        "title_artwork": artwork,
    }



# ---------------------------------------------------------------- 组装入口


def _episode_tconsts(series_title_id: str) -> dict[tuple[int, int], str]:
    """从本地 imdb.db 取父剧集下 (season, episode) -> 单集 Title ID 映射。"""
    if not IMDB_DB.exists():
        logger.warning("未找到本地 imdb.db，单集 Title ID 无法解析")
        return {}
    with sqlite3.connect(f"file:{IMDB_DB}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT seasonNumber, episodeNumber, tconst
              FROM title_episode
             WHERE parentTconst = ?
            """,
            (series_title_id,),
        ).fetchall()
    return {
        (int(row[0]), int(row[1])): row[2]
        for row in rows
        if row[0] is not None and row[1] is not None
    }


def _parent_tconst(episode_title_id: str) -> str | None:
    """从本地 imdb.db 查单集的父剧集 Title ID。"""
    if not IMDB_DB.exists():
        logger.warning("未找到本地 imdb.db，父剧集 Title ID 无法解析")
        return None
    with sqlite3.connect(f"file:{IMDB_DB}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT parentTconst FROM title_episode WHERE tconst = ?",
            (episode_title_id,),
        ).fetchone()
    return row[0] if row else None


def build_episode_metadata(
    session: requests.Session, imdb_id: str, found: dict
) -> dict:
    """组装单个单集：父剧集仅用于补 original_language（归属关系在本地 imdb.db）。"""
    series = fetch_detail(session, "tv", int(found["show_id"]))

    parent_tconst = _parent_tconst(imdb_id)
    if parent_tconst is None:
        logger.warning(
            "单集 %s 在本地 imdb.db 未找到父剧集，剧照目录用 TMDB 剧集 ID 命名", imdb_id
        )
        parent_tconst = str(int(found["show_id"]))

    item = build_episode(
        found, parent_tconst, series.get("original_language"), episode_title_id=imdb_id
    )
    item["title_artwork"] = [
        {**row, "title_id": imdb_id} for row in item["title_artwork"]
    ]
    del item["season_number"], item["episode_number"]

    return {
        "title_basic": item["title_basic"],
        "title_artwork": item["title_artwork"],
        "title_cast": [],
        "episodes": [],
    }


def build_media_metadata(imdb_id: str, session: requests.Session | None = None) -> dict:
    """主入口：IMDb ID → TMDB 补充字段（与 library.db 字段对应）。

    电影 2 次请求（find + detail 挂载 images），
    剧集 3 次（find + detail 挂载 images + detail 挂载全部季），
    单集 2 次（find + 父剧集 detail 取 original_language）。
    """
    own_session = session is None
    session = session or _session()

    try:
        imdb_id = imdb_id.strip()
        media_type, found = find_media(session, imdb_id)

        if media_type == "episode":
            return build_episode_metadata(session, imdb_id, found)

        tmdb_id = int(found["id"])
        credits_key = "aggregate_credits" if media_type == "tv" else "credits"
        detail = fetch_detail(
            session,
            media_type,
            tmdb_id,
            append=f"images,{credits_key}",
        )
        images = detail.get("images") or {}

        metadata = {
            "title_basic": build_title_supplement(detail),
            "title_artwork": build_title_artwork(imdb_id, images),
            "title_cast": build_title_cast(
                imdb_id,
                (detail.get(credits_key) or {}).get("cast") or [],
            ),
            "episodes": [],
        }

        if media_type == "tv":
            series_language = detail.get("original_language")
            seasons = fetch_seasons(session, tmdb_id, detail.get("seasons") or [])
            tconsts = _episode_tconsts(imdb_id)
            metadata["episodes"] = [
                build_episode(
                    episode,
                    imdb_id,
                    series_language,
                    episode_title_id=tconsts.get(
                        (episode.get("season_number"), episode.get("episode_number"))
                    ),
                )
                for season in seasons
                for episode in season.get("episodes") or []
            ]

        return metadata
    finally:
        if own_session:
            session.close()


# ---------------------------------------------------------------- 图片下载


def _download_image(session: requests.Session, image_url: str, target_path: Path):
    """下载单张图片到临时文件后原子替换，带重试。"""
    temporary_path = target_path.with_suffix(target_path.suffix + ".part")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    last_error = None
    for _ in range(DOWNLOAD_ATTEMPTS):
        try:
            with session.get(image_url, timeout=REQUEST_TIMEOUT) as response:
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

    own_session = session is None
    session = session or _session()
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    try:
        for row in rows:
            target_path = _target_path(image_root, row)
            if target_path.exists():
                stats["skipped"] += 1
                continue

            try:
                _download_image(session, _download_url(row), target_path)
                stats["downloaded"] += 1
            except (requests.RequestException, ValueError) as error:
                logger.warning("下载失败 %s: %s", row["local_path"], error)
                stats["failed"] += 1
    finally:
        if own_session:
            session.close()

    return stats
