"""Compact candidate cards for the association picker."""

from connectors.tmdb import TMDB_IMAGE


def build_search_viewmodel(payload: dict, media_type: str) -> dict:
    items = []
    for item in payload.get("results") or []:
        name = item.get("title" if media_type == "movie" else "name") or "未命名作品"
        original = item.get("original_title" if media_type == "movie" else "original_name") or ""
        date = item.get("release_date" if media_type == "movie" else "first_air_date") or ""
        poster = item.get("poster_path")
        items.append({
            "tmdb_id": item["id"],
            "name": name,
            "original_name": original if original != name else "",
            "year": date[:4],
            "overview": item.get("overview") or "暂无简介。",
            "poster_url": f"{TMDB_IMAGE}/w92{poster}" if poster else None,
            "localized_poster_url": f"/manager/titles/poster?type={media_type}&tmdb_id={item['id']}",
        })
    page = payload.get("page", 1)
    return {
        "items": items,
        "page": page,
        "has_more": page < min(payload.get("total_pages", 0), 500),
    }


def build_selection_viewmodel(title: dict) -> dict:
    return {
        "title_id": title["title_id"],
        "name": title["display_title"],
        "year": title["start_year"],
        "media_type": "movie" if title["title_type"] == "movie" else "tv",
    }
