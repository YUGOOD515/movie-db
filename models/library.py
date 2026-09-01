import sqlite3
from contextlib import closing
from pathlib import Path

from models.database import connect_writable


RATING_COLUMNS = {
    "mdblist_score",
    "mdblist_score_average",
    "rt_critics_score",
    "rt_critics_votes",
    "rt_audience_score",
    "rt_audience_votes",
    "metacritic_score",
    "metacritic_votes",
    "metacritic_user_score",
    "metacritic_user_votes",
    "trakt_score",
    "trakt_votes",
    "letterboxd_score",
    "letterboxd_votes",
    "roger_ebert_rating",
    "myanimelist_score",
    "myanimelist_votes",
}


def save_library_playback_progress(
    database_path: str | Path,
    file_id: int,
    position_ms: int,
    playback_id: int | None = None,
):
    """Insert once per playback session, then update that history row."""
    position_ms = max(int(position_ms), 0)
    with closing(connect_writable(database_path)) as db, db:
        if playback_id is None:
            cursor = db.execute(
                """
                INSERT INTO playback_progress (file_id, position_ms)
                VALUES (?, ?)
                """,
                (file_id, position_ms),
            )
            return cursor.lastrowid

        cursor = db.execute(
            """
            UPDATE playback_progress
               SET position_ms = ?,
                   watched_at = CURRENT_TIMESTAMP
             WHERE playback_id = ?
               AND file_id = ?
            """,
            (position_ms, playback_id, file_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("播放记录不存在。")
        return playback_id


def toggle_library_collection_item(
    database_path: str | Path,
    collection_id: int,
    title_id: str,
):
    with closing(connect_writable(database_path)) as db, db:
        collection = db.execute(
            """
            SELECT collection_id, title_type
              FROM collection
             WHERE collection_id = ?
            """,
            (collection_id,),
        ).fetchone()
        title = db.execute(
            "SELECT title_id, title_type FROM title_basic WHERE title_id = ?",
            (title_id,),
        ).fetchone()
        if collection is None or title is None:
            raise ValueError("收藏夹或影视条目不存在。")

        if title["title_type"] == "episode":
            raise ValueError("单集不能直接收藏，请收藏所属剧集。")
        if collection["title_type"] not in (None, title["title_type"]):
            raise ValueError("该收藏夹不接受此类影视条目。")

        existing = db.execute(
            """
            SELECT 1
              FROM collection_item
             WHERE collection_id = ?
               AND title_id = ?
            """,
            (collection_id, title_id),
        ).fetchone()
        if existing:
            db.execute(
                """
                DELETE FROM collection_item
                 WHERE collection_id = ?
                   AND title_id = ?
                """,
                (collection_id, title_id),
            )
            return False

        db.execute(
            """
            INSERT INTO collection_item (collection_id, title_id)
            VALUES (?, ?)
            """,
            (collection_id, title_id),
        )
        return True


def set_library_primary_artwork(
    database_path: str | Path,
    title_id: str,
    artwork_type: str,
    position: int,
):
    set_library_primary_artworks(
        database_path,
        title_id,
        [(artwork_type, position)],
    )


def set_library_primary_artworks(
    database_path: str | Path,
    title_id: str,
    selections: list[tuple[str, int]],
):
    """Apply every primary-artwork selection in one transaction."""
    with closing(connect_writable(database_path)) as db, db:
        for artwork_type, position in selections:
            _set_primary_artwork(db, title_id, artwork_type, position)


def replace_library_cast(
    database_path: str | Path,
    title_id: str,
    cast: list[dict],
) -> None:
    with closing(connect_writable(database_path)) as db, db:
        _replace_title_cast(db, title_id, cast)


def _replace_title_cast(
    db: sqlite3.Connection,
    title_id: str,
    cast: list[dict],
) -> None:
    db.execute("DELETE FROM title_cast WHERE title_id = ?", (title_id,))
    db.executemany(
        """
        INSERT INTO title_cast (
            title_id,
            tmdb_person_id,
            name,
            character_name,
            cast_order,
            remote_path,
            local_path
        ) VALUES (
            :title_id,
            :tmdb_person_id,
            :name,
            :character_name,
            :cast_order,
            :remote_path,
            :local_path
        )
        """,
        cast,
    )


def _set_primary_artwork(
    db: sqlite3.Connection,
    title_id: str,
    artwork_type: str,
    position: int,
):
    if artwork_type not in ("poster", "landscape_poster", "logo"):
        raise ValueError("图片类型无效。")
    if position < 1:
        raise ValueError("图片位置无效。")

    selected = db.execute(
        """
        SELECT position
          FROM title_artwork
         WHERE title_id = ?
           AND artwork_type = ?
           AND position = ?
        """,
        (title_id, artwork_type, position),
    ).fetchone()
    if selected is None:
        raise ValueError("图片不存在。")
    if position == 1:
        return

    primary = db.execute(
        """
        SELECT position
          FROM title_artwork
         WHERE title_id = ?
           AND artwork_type = ?
           AND position = 1
        """,
        (title_id, artwork_type),
    ).fetchone()
    if primary is None:
        db.execute(
            """
            UPDATE title_artwork
               SET position = 1
             WHERE title_id = ?
               AND artwork_type = ?
               AND position = ?
            """,
            (title_id, artwork_type, position),
        )
        return

    temporary_position = db.execute(
        """
        SELECT COALESCE(MAX(position), 0) + 1
          FROM title_artwork
         WHERE title_id = ?
           AND artwork_type = ?
        """,
        (title_id, artwork_type),
    ).fetchone()[0]
    db.execute(
        """
        UPDATE title_artwork
           SET position = ?
         WHERE title_id = ?
           AND artwork_type = ?
           AND position = 1
        """,
        (temporary_position, title_id, artwork_type),
    )
    db.execute(
        """
        UPDATE title_artwork
           SET position = 1
         WHERE title_id = ?
           AND artwork_type = ?
           AND position = ?
        """,
        (title_id, artwork_type, position),
    )
    db.execute(
        """
        UPDATE title_artwork
           SET position = ?
         WHERE title_id = ?
           AND artwork_type = ?
           AND position = ?
        """,
        (position, title_id, artwork_type, temporary_position),
    )


def import_media_bundle(
    database_path: str | Path | None = None,
    *,
    titles: list[dict],
    relations: list[dict],
    artwork: list[dict],
    target_title_id: str,
    alist_path: str,
    probe: dict,
    cast: list[dict] | None = None,
    ratings_by_title: dict[str, dict] | None = None,
    db: sqlite3.Connection | None = None,
) -> int:
    """Write one complete import bundle, owning or joining one transaction."""
    if (database_path is None) == (db is None):
        raise ValueError("必须且只能提供 database_path 或 db。")

    def _execute_bundle(connection):
        existing_file = connection.execute(
            "SELECT file_id, title_id FROM media_file WHERE alist_path = ?",
            (alist_path,),
        ).fetchone()
        if existing_file is not None and existing_file["title_id"] != target_title_id:
            raise ValueError("该 AList 文件已关联其他影视条目。")

        for title in titles:
            connection.execute(
                """
                INSERT INTO title_basic (
                    title_id,
                    tmdb_id,
                    title_type,
                    primary_title,
                    original_title,
                    original_language,
                    display_title,
                    is_adult,
                    start_year,
                    end_year,
                    runtime_minutes,
                    genres,
                    overview,
                    tagline,
                    average_rating,
                    num_votes
                ) VALUES (
                    :title_id,
                    :tmdb_id,
                    :title_type,
                    :primary_title,
                    :original_title,
                    :original_language,
                    :display_title,
                    :is_adult,
                    :start_year,
                    :end_year,
                    :runtime_minutes,
                    :genres,
                    :overview,
                    :tagline,
                    :average_rating,
                    :num_votes
                )
                ON CONFLICT (title_id) DO UPDATE SET
                    tmdb_id = COALESCE(excluded.tmdb_id, title_basic.tmdb_id),
                    title_type = excluded.title_type,
                    primary_title = excluded.primary_title,
                    original_title = excluded.original_title,
                    original_language = COALESCE(
                        excluded.original_language,
                        title_basic.original_language
                    ),
                    display_title = excluded.display_title,
                    is_adult = excluded.is_adult,
                    start_year = excluded.start_year,
                    end_year = excluded.end_year,
                    runtime_minutes = excluded.runtime_minutes,
                    genres = excluded.genres,
                    overview = COALESCE(excluded.overview, title_basic.overview),
                    tagline = COALESCE(excluded.tagline, title_basic.tagline),
                    average_rating = excluded.average_rating,
                    num_votes = excluded.num_votes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                title,
            )

        for rating_title_id, ratings in (ratings_by_title or {}).items():
            _update_title_ratings(connection, rating_title_id, ratings)

        for relation in relations:
            connection.execute(
                """
                INSERT INTO title_relation (
                    episode_id,
                    series_id,
                    season_number,
                    episode_number
                ) VALUES (
                    :episode_id,
                    :series_id,
                    :season_number,
                    :episode_number
                )
                ON CONFLICT (episode_id) DO UPDATE SET
                    series_id = excluded.series_id,
                    season_number = excluded.season_number,
                    episode_number = excluded.episode_number
                """,
                relation,
            )

        for image in artwork:
            connection.execute(
                """
                INSERT OR IGNORE INTO title_artwork (
                    title_id,
                    artwork_type,
                    position,
                    remote_path,
                    local_path
                ) VALUES (
                    :title_id,
                    :artwork_type,
                    :position,
                    :remote_path,
                    :local_path
                )
                """,
                image,
            )

        if cast is not None and titles:
            cast_title_id = titles[0]["title_id"]
            _replace_title_cast(connection, cast_title_id, cast)

        values = {
            "title_id": target_title_id,
            "alist_path": alist_path,
            "duration_ms": probe.get("duration_ms", probe.get("media_duration_ms")),
            "file_size_bytes": probe.get("file_size_bytes"),
            "bit_rate": probe.get("bit_rate"),
            "container_format": probe.get("container_format"),
            "video_codec": probe.get("video_codec"),
            "video_width": probe.get("video_width"),
            "video_height": probe.get("video_height"),
            "video_frame_rate": probe.get("video_frame_rate"),
            "video_pixel_format": probe.get("video_pixel_format"),
            "video_dynamic_range": probe.get("video_dynamic_range"),
            "audio_codec": probe.get("audio_codec"),
            "audio_channels": probe.get("audio_channels"),
            "audio_channel_layout": probe.get("audio_channel_layout"),
        }
        connection.execute(
            """
            INSERT INTO media_file (
                title_id,
                alist_path,
                duration_ms,
                file_size_bytes,
                bit_rate,
                container_format,
                video_codec,
                video_width,
                video_height,
                video_frame_rate,
                video_pixel_format,
                video_dynamic_range,
                audio_codec,
                audio_channels,
                audio_channel_layout,
                probed_at
            ) VALUES (
                :title_id,
                :alist_path,
                :duration_ms,
                :file_size_bytes,
                :bit_rate,
                :container_format,
                :video_codec,
                :video_width,
                :video_height,
                :video_frame_rate,
                :video_pixel_format,
                :video_dynamic_range,
                :audio_codec,
                :audio_channels,
                :audio_channel_layout,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (alist_path) DO UPDATE SET
                title_id = excluded.title_id,
                duration_ms = excluded.duration_ms,
                file_size_bytes = excluded.file_size_bytes,
                bit_rate = excluded.bit_rate,
                container_format = excluded.container_format,
                video_codec = excluded.video_codec,
                video_width = excluded.video_width,
                video_height = excluded.video_height,
                video_frame_rate = excluded.video_frame_rate,
                video_pixel_format = excluded.video_pixel_format,
                video_dynamic_range = excluded.video_dynamic_range,
                audio_codec = excluded.audio_codec,
                audio_channels = excluded.audio_channels,
                audio_channel_layout = excluded.audio_channel_layout,
                probed_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        return connection.execute(
            "SELECT file_id FROM media_file WHERE alist_path = ?",
            (alist_path,),
        ).fetchone()[0]

    if db is not None:
        return _execute_bundle(db)

    with closing(connect_writable(database_path)) as connection, connection:
        return _execute_bundle(connection)


def update_title_ratings(
    database_path: str | Path,
    title_id: str,
    ratings: dict,
) -> bool:
    """Update multi-platform ratings for a title in title_basic."""
    with closing(connect_writable(database_path)) as db, db:
        return _update_title_ratings(db, title_id, ratings)


def _update_title_ratings(db, title_id: str, ratings: dict) -> bool:
    updates = {key: value for key, value in ratings.items() if key in RATING_COLUMNS}
    if not updates:
        return False
    set_clause = ", ".join(f"{column} = :{column}" for column in updates)
    cursor = db.execute(
        f"""
        UPDATE title_basic
           SET {set_clause},
               updated_at = CURRENT_TIMESTAMP
         WHERE title_id = :title_id
        """,
        {"title_id": title_id, **updates},
    )
    return cursor.rowcount > 0
