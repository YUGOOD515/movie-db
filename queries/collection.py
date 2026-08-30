from sqlite3 import Connection

from queries._shared import dictionaries


def get_collections(db: Connection) -> list[dict]:
    rows = db.execute(
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
         GROUP BY collection.collection_id
         ORDER BY collection."order",
                  collection.collection_id
        """
    )
    return dictionaries(rows)


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
        collection_filter = "WHERE item.collection_id = ?"
        parameters = (collection_id,)
    rows = db.execute(
        f"""
        WITH selected_items AS (
            SELECT COALESCE(relation.series_id, item.title_id) AS title_id,
                   MAX(item.added_at) AS added_at
              FROM collection_item AS item
              LEFT JOIN title_relation AS relation
                ON relation.episode_id = item.title_id
              {collection_filter}
             GROUP BY COALESCE(relation.series_id, item.title_id)
        )
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
               selected.added_at,
               (SELECT artwork.local_path
                  FROM title_artwork AS artwork
                 WHERE artwork.title_id = title.title_id
                    AND artwork.artwork_type = 'poster'
                  ORDER BY artwork.position
                  LIMIT 1) AS poster_path
          FROM selected_items AS selected
          JOIN title_basic AS title
            ON title.title_id = selected.title_id
           AND title.title_type IN ('movie', 'series')
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


def query_collection(
    db: Connection,
    collection_id: int | None = None,
) -> dict | None:
    collection = None
    if collection_id is not None:
        collection = get_collection(db, collection_id)
        if collection is None:
            return None
    return {
        "collection": collection,
        "items": get_collection_items(db, collection_id),
        "collections": get_collections(db),
    }
