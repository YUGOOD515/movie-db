from sqlite3 import Connection

from queries._shared import dictionaries, query_title_cards


SORT_EXPRESSIONS = {
    "added": (
        "title.added_at DESC, title.updated_at DESC, "
        "title.display_title COLLATE NOCASE"
    ),
    "rating": (
        "title.average_rating IS NULL, title.average_rating DESC, "
        "title.num_votes DESC, title.display_title COLLATE NOCASE"
    ),
    "year": (
        "title.start_year IS NULL, title.start_year DESC, "
        "title.display_title COLLATE NOCASE"
    ),
    "name": "title.display_title COLLATE NOCASE ASC",
}


def query_catalog(db: Connection, filters: dict) -> list[dict]:
    conditions = ["title.title_type IN ('movie', 'series')"]
    parameters = []

    if filters["type"] in ("movie", "series"):
        conditions.append("title.title_type = ?")
        parameters.append(filters["type"])
    if filters["genre"]:
        conditions.append(
            "INSTR(',' || REPLACE(title.genres, ' ', '') || ',', "
            "       ',' || REPLACE(?, ' ', '') || ',') > 0"
        )
        parameters.append(filters["genre"])
    if filters["year"] is not None:
        conditions.append("title.start_year = ?")
        parameters.append(filters["year"])
    if filters["rating"] is not None:
        conditions.append("title.average_rating >= ?")
        parameters.append(filters["rating"])
    if filters["language"]:
        conditions.append("title.original_language = ?")
        parameters.append(filters["language"])

    return query_title_cards(
        db,
        where_sql=" AND ".join(conditions),
        parameters=tuple(parameters),
        order_sql=SORT_EXPRESSIONS[filters["sort"]],
    )


def get_catalog_options(db: Connection) -> dict:
    rows = dictionaries(
        db.execute(
            """
            SELECT title_type,
                   genres,
                   start_year,
                   original_language
              FROM title_basic
             WHERE title_type IN ('movie', 'series')
            """
        )
    )
    return {"rows": rows}
