"""Search candidates and validate their identity against read-only IMDb data."""

import re
from contextlib import closing

from connectors import tmdb
from models.database import connect_imdb
from queries.imdb import get_imdb_title


def _validate_type(media_type):
    if media_type not in ("movie", "tv"):
        raise ValueError("请选择电影或剧集。")


def search_titles(query: str, media_type: str, page: str) -> dict:
    _validate_type(media_type)
    query = query.strip()
    if not query or len(query) > 100:
        raise ValueError("请输入 1–100 个字符的影片或剧集名称。")
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        raise ValueError("页码无效。") from None
    if not 1 <= page_number <= 500:
        raise ValueError("页码须在 1–500 之间。")
    return tmdb.search_media(query, media_type, page_number)


def select_title(imdb_database, media_type: str, tmdb_id: str) -> dict:
    _validate_type(media_type)
    if not re.fullmatch(r"[1-9][0-9]{0,9}", tmdb_id):
        raise ValueError("TMDB ID 无效。")
    title_id = tmdb.resolve_imdb_id(media_type, int(tmdb_id))
    if not re.fullmatch(r"tt[0-9]+", title_id):
        raise ValueError("该作品没有 IMDb 映射，请选择其他结果或手动填写 IMDb ID。")
    with closing(connect_imdb(imdb_database)) as db:
        title = get_imdb_title(db, title_id)
    if title is None:
        raise ValueError(f"本地 IMDb 数据库尚未收录 {title_id}，请更新本地数据后再导入。")
    expected_type = "movie" if media_type == "movie" else "series"
    if title["title_type"] != expected_type:
        raise ValueError("TMDB 与本地 IMDb 的作品类型不一致，无法关联。")
    tmdb.remember_media_identity(title_id, media_type, int(tmdb_id))
    return title
