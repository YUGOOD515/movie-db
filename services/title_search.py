"""Search TMDB candidates and resolve the selected title to its IMDb ID."""

import re

from connectors import tmdb


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


def select_title(media_type: str, tmdb_id: str) -> dict:
    _validate_type(media_type)
    if not re.fullmatch(r"[1-9][0-9]{0,9}", tmdb_id):
        raise ValueError("TMDB ID 无效。")
    title = tmdb.fetch_title_summary(media_type, int(tmdb_id))
    if not re.fullmatch(r"tt[0-9]+", title["title_id"]):
        raise ValueError("该作品没有 IMDb 映射，请选择其他结果或手动填写 IMDb ID。")
    tmdb.remember_media_identity(title["title_id"], media_type, int(tmdb_id))
    return title
