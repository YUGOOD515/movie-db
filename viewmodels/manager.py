from collections import defaultdict

from flask import url_for

from viewmodels.cards import format_year


def build_manager_viewmodel(
    raw: dict,
    *,
    active_tab: str = "overview",
    return_to: str = "/media",
    missing: dict[str, int] | None = None,
) -> dict:
    artwork_by_title = defaultdict(lambda: defaultdict(list))
    for image in raw["artwork"]:
        artwork_by_title[image["title_id"]][image["artwork_type"]].append(image)

    items = []
    for title in raw["titles"]:
        images = artwork_by_title[title["title_id"]]
        primary = {
            artwork_type: entries[0]["local_path"] if entries else None
            for artwork_type, entries in images.items()
        }
        items.append(
            {
                "media_id": title["title_id"],
                "media_type": (
                    "tv" if title["title_type"] == "series" else "movie"
                ),
                "type_label": (
                    "剧集" if title["title_type"] == "series" else "电影"
                ),
                "name": title["display_title"],
                "original_name": title.get("primary_title") or "",
                "title_id": title["title_id"],
                "tmdb_id": title.get("tmdb_id"),
                "year": format_year(title),
                "poster_path": primary.get("poster"),
                "backdrop_path": primary.get("landscape_poster"),
                "logo_path": primary.get("logo"),
                "detail_url": url_for(
                    "title_detail", title_id=title["title_id"]
                ),
                "return_to": return_to,
                "missing": (missing or {}).get(title["title_id"], 0),
                "image_options": {
                    option: [
                        {
                            **image,
                            "image_position": image["position"],
                            "is_current": image["position"] == 1,
                            "status_label": "当前使用" if image["position"] == 1 else "点击试用",
                        }
                        for image in images[artwork_type]
                    ]
                    for option, artwork_type in (
                        ("posters", "poster"),
                        ("backdrops", "landscape_poster"),
                        ("logos", "logo"),
                    )
                },
            }
        )
    return {
        "media_items": items,
        "query": raw["query"],
        "media_type": (
            "tv" if raw["title_type"] == "series" else raw["title_type"]
        ),
        "active_tab": active_tab,
    }
