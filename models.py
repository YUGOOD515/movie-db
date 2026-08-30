import sqlite3
from contextlib import closing
from pathlib import Path


def _connect_library(database_path: str | Path):
    database = Path(database_path).resolve()
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=rw",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def save_library_playback_progress(
    database_path: str | Path,
    file_id: int,
    position_ms: int,
    playback_id: int | None = None,
):
    """Insert once per playback session, then update that history row."""
    position_ms = max(int(position_ms), 0)
    with closing(_connect_library(database_path)) as db, db:
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
    with closing(_connect_library(database_path)) as db, db:
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

        scoped_type = "series" if title["title_type"] == "episode" else title["title_type"]
        if collection["title_type"] not in (None, scoped_type):
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
    if artwork_type not in ("poster", "landscape_poster", "logo"):
        raise ValueError("图片类型无效。")
    if position < 1:
        raise ValueError("图片位置无效。")

    with closing(_connect_library(database_path)) as db, db:
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
