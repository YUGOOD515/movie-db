from collections.abc import Iterable
from sqlite3 import Connection, Row


CARD_ARTWORK_SQL = """
       (SELECT artwork.local_path
          FROM title_artwork AS artwork
         WHERE artwork.title_id = title.title_id
           AND artwork.artwork_type = 'poster'
         ORDER BY artwork.position
         LIMIT 1) AS poster_path,
       (SELECT artwork.local_path
          FROM title_artwork AS artwork
         WHERE artwork.title_id = title.title_id
           AND artwork.artwork_type = 'landscape_poster'
         ORDER BY artwork.position
         LIMIT 1) AS backdrop_path,
       (SELECT artwork.local_path
          FROM title_artwork AS artwork
         WHERE artwork.title_id = title.title_id
           AND artwork.artwork_type = 'logo'
         ORDER BY artwork.position
         LIMIT 1) AS logo_path
"""


def dictionaries(rows: Iterable[Row]) -> list[dict]:
    return [dict(row) for row in rows]


def query_title_cards(
    db: Connection,
    *,
    where_sql: str,
    parameters: tuple = (),
    order_sql: str = "title.display_title COLLATE NOCASE",
    limit: int | None = None,
) -> list[dict]:
    limit_sql = ""
    query_parameters = list(parameters)
    if limit is not None:
        limit_sql = "LIMIT ?"
        query_parameters.append(limit)

    rows = db.execute(
        f"""
        SELECT title.title_id,
               title.tmdb_id,
               title.title_type,
               title.primary_title,
               title.original_title,
               title.original_language,
               title.display_title,
               title.start_year,
               title.end_year,
               title.genres,
               title.average_rating,
               title.num_votes,
               title.added_at,
               {CARD_ARTWORK_SQL}
          FROM title_basic AS title
         WHERE {where_sql}
         ORDER BY {order_sql}
         {limit_sql}
        """,
        tuple(query_parameters),
    )
    return dictionaries(rows)
