from sqlite3 import Connection

from queries._shared import dictionaries
from queries.collection import get_title_collections


def get_title(db: Connection, title_id: str) -> dict | None:
    row = db.execute(
        "SELECT * FROM title_basic WHERE title_id = ?",
        (title_id,),
    ).fetchone()
    return dict(row) if row else None


def get_title_id_by_tmdb(
    db: Connection,
    title_type: str,
    tmdb_id: int,
) -> str | None:
    row = db.execute(
        """
        SELECT title_id
          FROM title_basic
         WHERE title_type = ?
           AND tmdb_id = ?
        """,
        (title_type, tmdb_id),
    ).fetchone()
    return row["title_id"] if row else None


def get_episode_relation(db: Connection, episode_id: str) -> dict | None:
    row = db.execute(
        """
        SELECT episode_id, series_id, season_number, episode_number
          FROM title_relation
         WHERE episode_id = ?
        """,
        (episode_id,),
    ).fetchone()
    return dict(row) if row else None


def get_artwork(db: Connection, title_id: str) -> list[dict]:
    return dictionaries(
        db.execute(
            """
            SELECT title_id,
                   artwork_type,
                   position,
                   remote_path,
                   local_path
              FROM title_artwork
             WHERE title_id = ?
             ORDER BY artwork_type, position
            """,
            (title_id,),
        )
    )


def get_title_files(db: Connection, title_id: str) -> list[dict]:
    return dictionaries(
        db.execute(
            """
            SELECT file.*,
                   progress.playback_id,
                   progress.position_ms,
                   progress.watched_at
              FROM media_file AS file
              LEFT JOIN playback_progress AS progress
                ON progress.playback_id = (
                       SELECT latest.playback_id
                         FROM playback_progress AS latest
                        WHERE latest.file_id = file.file_id
                        ORDER BY latest.watched_at DESC,
                                 latest.playback_id DESC
                        LIMIT 1
                   )
             WHERE file.title_id = ?
             ORDER BY file.file_id
            """,
            (title_id,),
        )
    )


def get_series_episode_rows(db: Connection, series_id: str) -> list[dict]:
    return dictionaries(
        db.execute(
            """
            SELECT episode.title_id AS episode_id,
                   episode.tmdb_id AS episode_tmdb_id,
                   episode.primary_title AS episode_primary_title,
                   episode.original_title AS episode_original_title,
                   episode.display_title AS episode_display_title,
                   episode.original_language AS episode_original_language,
                   episode.start_year AS episode_start_year,
                   episode.runtime_minutes AS episode_runtime_minutes,
                   episode.overview AS episode_overview,
                   episode.average_rating AS episode_average_rating,
                   episode.num_votes AS episode_num_votes,
                   relation.season_number,
                   relation.episode_number,
                   (SELECT artwork.local_path
                      FROM title_artwork AS artwork
                     WHERE artwork.title_id = episode.title_id
                       AND artwork.artwork_type = 'still'
                     ORDER BY artwork.position
                     LIMIT 1) AS still_path,
                   file.file_id,
                   file.alist_path,
                   file.duration_ms,
                   file.file_size_bytes,
                   file.bit_rate,
                   file.container_format,
                   file.video_codec,
                   file.video_width,
                   file.video_height,
                   file.video_frame_rate,
                   file.video_pixel_format,
                   file.video_dynamic_range,
                   file.audio_codec,
                   file.audio_channels,
                   file.audio_channel_layout,
                   file.probed_at,
                   progress.playback_id,
                   progress.position_ms,
                   progress.watched_at
              FROM title_relation AS relation
              JOIN title_basic AS episode
                ON episode.title_id = relation.episode_id
              LEFT JOIN media_file AS file
                ON file.title_id = episode.title_id
              LEFT JOIN playback_progress AS progress
                ON progress.playback_id = (
                       SELECT latest.playback_id
                         FROM playback_progress AS latest
                        WHERE latest.file_id = file.file_id
                        ORDER BY latest.watched_at DESC,
                                 latest.playback_id DESC
                        LIMIT 1
                   )
             WHERE relation.series_id = ?
             ORDER BY relation.season_number,
                      relation.episode_number,
                      file.file_id
            """,
            (series_id,),
        )
    )


def get_playable_file(db: Connection, file_id: int) -> dict | None:
    row = db.execute(
        """
        SELECT file.*,
               title.title_type,
               title.display_title,
               progress.position_ms,
               progress.watched_at
          FROM media_file AS file
          JOIN title_basic AS title
            ON title.title_id = file.title_id
          LEFT JOIN playback_progress AS progress
            ON progress.playback_id = (
                   SELECT latest.playback_id
                     FROM playback_progress AS latest
                    WHERE latest.file_id = file.file_id
                    ORDER BY latest.watched_at DESC,
                             latest.playback_id DESC
                    LIMIT 1
               )
         WHERE file.file_id = ?
        """,
        (file_id,),
    ).fetchone()
    return dict(row) if row else None


def query_title(
    db: Connection,
    title_id: str,
    *,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> dict | None:
    requested = get_title(db, title_id)
    if requested is None:
        return None

    requested_relation = None
    root = requested
    if requested["title_type"] == "episode":
        requested_relation = get_episode_relation(db, title_id)
        if requested_relation is None:
            return None
        root = get_title(db, requested_relation["series_id"])
        if root is None:
            return None
        season_number = requested_relation["season_number"]
        episode_number = requested_relation["episode_number"]

    episode_rows = []
    files = []
    relevant_title_ids = [root["title_id"]]
    if root["title_type"] == "series":
        episode_rows = get_series_episode_rows(db, root["title_id"])
    else:
        files = get_title_files(db, root["title_id"])

    collection_data = get_title_collections(db, relevant_title_ids)
    return {
        "requested": requested,
        "root": root,
        "requested_relation": requested_relation,
        "artwork": get_artwork(db, root["title_id"]),
        "files": files,
        "episode_rows": episode_rows,
        "season_number": season_number,
        "episode_number": episode_number,
        **collection_data,
    }
