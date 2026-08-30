from sqlite3 import Connection

from queries._shared import dictionaries


def get_collection(db: Connection, collection_id: int) -> dict | None:
    row = db.execute(
        """
        SELECT collection.collection_id,
               collection.name,
               collection.collection_type,
               collection.title_type,
               collection.icon_name,
               collection."order",
               COUNT(item.title_id) AS item_count
          FROM collection
          LEFT JOIN collection_item AS item
            ON item.collection_id = collection.collection_id
         WHERE collection.collection_id = ?
         GROUP BY collection.collection_id
        """,
        (collection_id,),
    ).fetchone()
    return dict(row) if row else None


def get_collection_items(
    db: Connection,
    collection_id: int | None = None,
) -> list[dict]:
    collection_filter = ""
    parameters = ()
    if collection_id is not None:
        collection_filter = "WHERE collection_id = ?"
        parameters = (collection_id,)
    rows = db.execute(
        f"""
        WITH selected_items AS (
            SELECT title_id,
                   MAX(added_at) AS added_at
              FROM collection_item
              {collection_filter}
             GROUP BY title_id
        )
        SELECT item.title_id AS item_title_id,
               item.title_type AS item_title_type,
               item.display_title AS item_display_title,
               relation.season_number,
               relation.episode_number,
               series.title_id AS series_id,
               series.display_title AS series_title,
               COALESCE(series.title_id, item.title_id) AS title_id,
               COALESCE(series.title_type, item.title_type) AS title_type,
               COALESCE(series.display_title, item.display_title)
                   AS display_title,
               COALESCE(series.start_year, item.start_year) AS start_year,
               COALESCE(series.end_year, item.end_year) AS end_year,
               COALESCE(series.average_rating, item.average_rating)
                   AS average_rating,
               selected.added_at,
               (SELECT artwork.local_path
                  FROM title_artwork AS artwork
                 WHERE artwork.title_id = COALESCE(series.title_id, item.title_id)
                   AND artwork.artwork_type = 'poster'
                 ORDER BY artwork.position
                 LIMIT 1) AS poster_path
          FROM selected_items AS selected
          JOIN title_basic AS item
            ON item.title_id = selected.title_id
          LEFT JOIN title_relation AS relation
            ON relation.episode_id = item.title_id
          LEFT JOIN title_basic AS series
            ON series.title_id = relation.series_id
         ORDER BY selected.added_at DESC,
                  display_title COLLATE NOCASE
        """,
        parameters,
    )
    return dictionaries(rows)


def get_title_collections(
    db: Connection,
    relevant_title_ids: list[str],
) -> dict:
    collections = dictionaries(
        db.execute(
            """
            SELECT collection_id,
                   name,
                   collection_type,
                   title_type,
                   icon_name,
                   "order"
              FROM collection
             ORDER BY "order", collection_id
            """
        )
    )
    memberships = set()
    if relevant_title_ids:
        placeholders = ", ".join("?" for _ in relevant_title_ids)
        rows = db.execute(
            f"""
            SELECT collection_id, title_id
              FROM collection_item
             WHERE title_id IN ({placeholders})
            """,
            tuple(relevant_title_ids),
        )
        memberships = {
            (row["collection_id"], row["title_id"])
            for row in rows
        }
    return {"collections": collections, "memberships": memberships}
