from sqlite3 import Connection

from queries._shared import dictionaries, query_title_cards


def get_all_title_ids(db: Connection) -> list[str]:
    return [row[0] for row in db.execute("SELECT title_id FROM title_basic")]


def get_rating_title_ids(db: Connection) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            """
            SELECT title_id
              FROM title_basic
             WHERE title_type IN ('movie', 'series')
             ORDER BY title_id
            """
        )
    ]


def get_douban_refresh_targets(db: Connection) -> list[dict]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT title_id, title_type, douban_id FROM title_basic "
            "WHERE title_type IN ('movie', 'series') ORDER BY title_id"
        )
    ]


def get_tone_artwork_paths(db: Connection) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT local_path FROM title_artwork WHERE artwork_type IN ('poster', 'landscape_poster')"
        )
    ]


def query_manager(
    db: Connection,
    *,
    query: str | None = None,
    title_type: str = "all",
) -> dict:
    conditions = ["title.title_type IN ('movie', 'series')"]
    parameters = []
    if title_type in ("movie", "series"):
        conditions.append("title.title_type = ?")
        parameters.append(title_type)
    if query:
        conditions.append(
            "(" 
            "title.display_title LIKE ? OR "
            "title.primary_title LIKE ? OR "
            "title.original_title LIKE ? OR "
            "title.title_id LIKE ? OR "
            "CAST(title.tmdb_id AS TEXT) LIKE ?"
            ")"
        )
        pattern = f"%{query}%"
        parameters.extend([pattern] * 5)

    titles = query_title_cards(
        db,
        where_sql=" AND ".join(conditions),
        parameters=tuple(parameters),
        order_sql="title.display_title COLLATE NOCASE",
        include_artwork=False,
    )
    title_ids = [title["title_id"] for title in titles]
    artwork = []
    if title_ids:
        placeholders = ", ".join("?" for _ in title_ids)
        artwork = dictionaries(
            db.execute(
                f"""
                SELECT title_id,
                       artwork_type,
                       position,
                       remote_path,
                       local_path
                  FROM title_artwork
                 WHERE title_id IN ({placeholders})
                   AND artwork_type IN (
                       'poster', 'landscape_poster', 'logo'
                   )
                 ORDER BY title_id,
                          artwork_type,
                          position
                """,
                tuple(title_ids),
            )
        )

    return {
        "titles": titles,
        "artwork": artwork,
        "query": query or "",
        "title_type": title_type,
    }
