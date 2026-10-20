from sqlite3 import Connection

from queries._shared import CARD_ARTWORK_SQL, dictionaries, query_title_cards
from queries.collection import get_collections


def get_recently_watched(db: Connection, limit: int = 8) -> list[dict]:
    rows = db.execute(
        f"""
        WITH watched AS (
            SELECT progress.playback_id,
                   progress.watched_at,
                   progress.position_ms,
                   file.file_id,
                   file.duration_ms,
                   watched_title.title_id AS watched_title_id,
                   watched_title.title_type AS watched_title_type,
                   watched_title.display_title AS episode_title,
                   relation.series_id,
                   relation.season_number,
                   relation.episode_number,
                   COALESCE(relation.series_id, watched_title.title_id)
                       AS library_title_id
              FROM playback_progress AS progress
              JOIN media_file AS file
                ON file.file_id = progress.file_id
              JOIN title_basic AS watched_title
                ON watched_title.title_id = file.title_id
              LEFT JOIN title_relation AS relation
                ON relation.episode_id = watched_title.title_id
        ),
        ranked AS (
            SELECT watched.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY watched.library_title_id
                           ORDER BY watched.watched_at DESC,
                                    watched.playback_id DESC
                   ) AS work_rank
              FROM watched
        )
        SELECT title.title_id,
               title.title_type,
               title.display_title,
               title.start_year,
               title.end_year,
               title.average_rating,
               ranked.watched_title_id,
               ranked.episode_title,
               ranked.season_number,
               ranked.episode_number,
               ranked.file_id,
               ranked.position_ms,
               ranked.duration_ms,
               ranked.watched_at,
               {CARD_ARTWORK_SQL}
          FROM ranked
          JOIN title_basic AS title
            ON title.title_id = ranked.library_title_id
         WHERE ranked.work_rank = 1
         ORDER BY ranked.watched_at DESC,
                  ranked.playback_id DESC
         LIMIT ?
        """,
        (limit,),
    )
    return dictionaries(rows)


def get_recently_added(
    db: Connection,
    limit: int | None = 12,
) -> list[dict]:
    return query_title_cards(
        db,
        where_sql="title.title_type IN ('movie', 'series')",
        order_sql=(
            "title.added_at DESC, title.updated_at DESC, "
            "title.display_title COLLATE NOCASE"
        ),
        limit=limit,
    )


def get_genre_titles(db: Connection, title_type: str) -> list[dict]:
    return query_title_cards(
        db,
        where_sql="title.title_type = ? AND title.genres IS NOT NULL",
        parameters=(title_type,),
        order_sql=(
            "title.average_rating DESC, title.num_votes DESC, "
            "title.display_title COLLATE NOCASE"
        ),
    )


def get_unwatched(
    db: Connection,
    limit: int | None = 12,
) -> list[dict]:
    return query_title_cards(
        db,
        where_sql="""
            (
                title.title_type = 'movie'
                AND NOT EXISTS (
                    SELECT 1
                      FROM media_file AS file
                      JOIN playback_progress AS progress
                        ON progress.file_id = file.file_id
                     WHERE file.title_id = title.title_id
                )
            )
            OR
            (
                title.title_type = 'series'
                AND NOT EXISTS (
                    SELECT 1
                      FROM title_relation AS relation
                      JOIN media_file AS file
                        ON file.title_id = relation.episode_id
                      JOIN playback_progress AS progress
                        ON progress.file_id = file.file_id
                     WHERE relation.series_id = title.title_id
                )
            )
        """,
        order_sql=(
            "title.added_at DESC, title.updated_at DESC, "
            "title.display_title COLLATE NOCASE"
        ),
        limit=limit,
    )


def get_top_rated_movies(
    db: Connection,
    limit: int | None = 9,
    minimum_votes: int = 1000,
) -> list[dict]:
    return query_title_cards(
        db,
        where_sql=(
            "title.title_type = 'movie' "
            "AND title.average_rating IS NOT NULL "
            "AND title.num_votes >= ?"
        ),
        parameters=(minimum_votes,),
        order_sql=(
            "title.average_rating DESC, title.num_votes DESC, "
            "title.display_title COLLATE NOCASE"
        ),
        limit=limit,
    )


def get_featured_titles(db: Connection, limit: int = 6) -> list[dict]:
    """首页主视觉：最近入库且有背景图的电影与剧集，附简介与可播放文件。"""
    rows = query_title_cards(
        db,
        where_sql="""
            title.title_type IN ('movie', 'series')
            AND EXISTS (
                SELECT 1
                  FROM title_artwork AS artwork
                 WHERE artwork.title_id = title.title_id
                   AND artwork.artwork_type = 'landscape_poster'
            )
        """,
        order_sql=(
            "title.added_at DESC, title.updated_at DESC, "
            "title.display_title COLLATE NOCASE"
        ),
        limit=limit,
    )
    if not rows:
        return rows
    placeholders = ", ".join("?" for _ in rows)
    details = {
        row["title_id"]: dict(row)
        for row in db.execute(
            f"""
            SELECT title.title_id,
                   title.overview,
                   title.tagline,
                   title.runtime_minutes,
                   (SELECT MIN(file.file_id)
                      FROM media_file AS file
                     WHERE file.title_id = title.title_id) AS file_id,
                   EXISTS (
                       SELECT 1
                         FROM title_relation AS relation
                         JOIN media_file AS file
                           ON file.title_id = relation.episode_id
                        WHERE relation.series_id = title.title_id
                   ) AS has_episode_files
              FROM title_basic AS title
             WHERE title.title_id IN ({placeholders})
            """,
            tuple(row["title_id"] for row in rows),
        )
    }
    return [{**row, **details.get(row["title_id"], {})} for row in rows]


def query_home(db: Connection) -> dict:
    return {
        "featured": get_featured_titles(db),
        "recent_watch": get_recently_watched(db),
        "collections": get_collections(db),
        "recent_added": get_recently_added(db),
        "movie_genres": get_genre_titles(db, "movie"),
        "series_genres": get_genre_titles(db, "series"),
        "unwatched": get_unwatched(db),
        "top_movies": get_top_rated_movies(db),
    }
