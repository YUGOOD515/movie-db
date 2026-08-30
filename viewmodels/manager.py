from collections import defaultdict

from flask import url_for

from viewmodels.cards import format_year


def build_manager_viewmodel(raw: dict) -> dict:
    artwork_by_title = defaultdict(lambda: defaultdict(list))
    for image in raw["artwork"]:
        artwork_by_title[image["title_id"]][image["artwork_type"]].append(image)

    items = []
    for title in raw["titles"]:
        images = artwork_by_title[title["title_id"]]
        items.append(
            {
                "media_id": title["title_id"],
                "media_type": (
                    "tv" if title["title_type"] == "series" else "movie"
                ),
                "name": title["display_title"],
                "original_name": title.get("primary_title") or "",
                "title_id": title["title_id"],
                "tmdb_id": title.get("tmdb_id"),
                "year": format_year(title),
                "poster_path": title.get("poster_path"),
                "backdrop_path": title.get("backdrop_path"),
                "logo_path": title.get("logo_path"),
                "detail_url": url_for(
                    "title_detail", title_id=title["title_id"]
                ),
                "image_options": {
                    "posters": [
                        {**image, "image_position": image["position"]}
                        for image in images["poster"]
                    ],
                    "backdrops": [
                        {**image, "image_position": image["position"]}
                        for image in images["landscape_poster"]
                    ],
                    "logos": [
                        {**image, "image_position": image["position"]}
                        for image in images["logo"]
                    ],
                },
            }
        )
    return {
        "media_items": items,
        "query": raw["query"],
        "media_type": (
            "tv" if raw["title_type"] == "series" else raw["title_type"]
        ),
    }
