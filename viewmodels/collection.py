from viewmodels.cards import build_poster_card


def build_collection_viewmodel(
    raw: dict,
) -> dict:
    collection = raw["collection"]
    rows = raw["items"]
    return {
        "collection_id": collection["collection_id"] if collection else None,
        "title": collection["name"] if collection else "全部收藏",
        "description": f"{len(rows)} 部作品",
        "icon": (collection.get("icon_name") if collection else None)
        or "collection",
        "items": [build_poster_card(row) for row in rows],
        "collections": raw["collections"],
    }
