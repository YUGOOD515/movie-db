"""Match provisional episodes against the local IMDb database.

For every episode with match_status='provisional' (no imdb_id), look up the
episode tconst in imdb.db title_episode via its matched parent series, then
write the imdb_id back to movie.db so the title becomes 'matched'.

Usage:
    uv run python scripts/match_provisional_episodes.py
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOVIE_DB = PROJECT_ROOT / "movie.db"
IMDB_DB = PROJECT_ROOT / "imdb.db"
BACKUP_DIR = PROJECT_ROOT / "backups"


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    resolved = path.resolve()
    mode = "ro" if readonly else "rwc"
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode={mode}",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    return connection


def main():
    backup_path = BACKUP_DIR / (
        "movie_backup_before_episode_match_"
        + time.strftime("%Y%m%d_%H%M%S")
        + ".db"
    )
    BACKUP_DIR.mkdir(exist_ok=True)
    shutil.copy2(MOVIE_DB, backup_path)
    print(f"backup: {backup_path.name}")

    with _connect(IMDB_DB, readonly=True) as imdb_conn, \
            _connect(MOVIE_DB) as movie_conn:
        episodes = movie_conn.execute(
            """
            SELECT e.media_id, e.display_title, e.season_number,
                   e.episode_number, ps.imdb_id AS series_imdb,
                   ps.display_title AS series_title
            FROM media e
            JOIN media ps ON ps.media_id = e.parent_media_id
            WHERE e.match_status = 'provisional'
              AND ps.imdb_id IS NOT NULL
            ORDER BY ps.display_title, e.season_number, e.episode_number
            """
        ).fetchall()
        print(f"provisional episodes with matched parent: {len(episodes)}")

        matched = 0
        unmatched = []
        with movie_conn:
            for episode in episodes:
                row = imdb_conn.execute(
                    """
                    SELECT tconst FROM title_episode
                    WHERE parentTconst = ?
                      AND seasonNumber = ?
                      AND episodeNumber = ?
                    """,
                    (
                        episode["series_imdb"],
                        episode["season_number"],
                        episode["episode_number"],
                    ),
                ).fetchone()

                if row is None:
                    unmatched.append(
                        (
                            episode["series_title"],
                            episode["season_number"],
                            episode["episode_number"],
                            episode["display_title"],
                        )
                    )
                    continue

                exists = imdb_conn.execute(
                    "SELECT 1 FROM title_basics WHERE tconst = ?",
                    (row["tconst"],),
                ).fetchone()
                if exists is None:
                    unmatched.append(
                        (
                            episode["series_title"],
                            episode["season_number"],
                            episode["episode_number"],
                            f"{episode['display_title']} (tconst missing in title_basics)",
                        )
                    )
                    continue

                movie_conn.execute(
                    """
                    UPDATE media
                    SET imdb_id = ?, match_status = 'matched'
                    WHERE media_id = ?
                    """,
                    (row["tconst"], episode["media_id"]),
                )
                matched += 1
                print(
                    f"  [ok] {row['tconst']} "
                    f"{episode['series_title']} "
                    f"S{episode['season_number']:02d}"
                    f"E{episode['episode_number']:02d} "
                    f"{episode['display_title']}"
                )

        print(f"matched: {matched}/{len(episodes)}")
        for item in unmatched:
            print(f"  [unmatched] {item}")

        remaining = movie_conn.execute(
            "SELECT COUNT(*) FROM media WHERE match_status = 'provisional'"
        ).fetchone()[0]
        print(f"remaining provisional: {remaining}")

        result = movie_conn.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"integrity_check: {result}")


if __name__ == "__main__":
    main()
