from viewmodels.cards import build_poster_card


VALID_TYPES = {"all", "movie", "series"}
VALID_SORTS = {"added", "rating", "year", "name"}


def parse_catalog_filters(arguments) -> dict:
    title_type = arguments.get("type", "all")
    if title_type == "tv":
        title_type = "series"
    if title_type not in VALID_TYPES:
        title_type = "all"

    try:
        year = int(arguments["year"]) if arguments.get("year") else None
    except (TypeError, ValueError):
        year = None
    try:
        rating = float(arguments["rating"]) if arguments.get("rating") else None
    except (TypeError, ValueError):
        rating = None
    if rating is not None and not 0 <= rating <= 10:
        rating = None

    sort = arguments.get("sort", "added")
    if sort not in VALID_SORTS:
        sort = "added"
    return {
        "type": title_type,
        "genre": (arguments.get("genre") or "").strip() or None,
        "year": year,
        "rating": rating,
        "language": (arguments.get("language") or "").strip() or None,
        "sort": sort,
    }


def build_catalog_options(raw: dict) -> dict:
    genres = set()
    years = set()
    languages = set()
    for row in raw["rows"]:
        genres.update(
            genre.strip()
            for genre in (row.get("genres") or "").split(",")
            if genre.strip()
        )
        if row.get("start_year"):
            years.add(row["start_year"])
        if row.get("original_language"):
            languages.add(row["original_language"])
    return {
        "genres": sorted(genres),
        "years": sorted(years, reverse=True),
        "languages": sorted(languages),
    }


def build_catalog_viewmodel(
    rows: list[dict],
    options: dict,
    filters: dict,
) -> dict:
    return {
        "title": "类别筛选",
        "description": "按类型、类别、年份、评分与语言筛选本地影视库",
        "items": [build_poster_card(row, show_rating=True) for row in rows],
        "options": build_catalog_options(options),
        "filters": filters,
    }


def build_library_viewmodel(
    rows: list[dict],
    *,
    title: str,
    description: str,
    empty_message: str,
    show_rating: bool = False,
) -> dict:
    return {
        "title": title,
        "description": description,
        "empty_message": empty_message,
        "items": [
            build_poster_card(row, show_rating=show_rating) for row in rows
        ],
    }
