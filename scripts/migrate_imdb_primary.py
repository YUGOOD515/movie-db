"""Build and validate the IMDb-primary MovieDB beside the live database.

The live ``movie.db`` is never replaced by this script. It creates a SQLite
backup and a new side-by-side database, applies migrations 009 and 010, then
verifies data parity against both the legacy database and local ``imdb.db``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "migrations" / "009_create_imdb_primary_schema.sql"
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "010_migrate_imdb_primary_data.sql"


def _timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    resolved = path.resolve()
    if readonly:
        connection = sqlite3.connect(
            f"file:{resolved.as_posix()}?mode=ro",
            uri=True,
            timeout=30,
        )
    else:
        connection = sqlite3.connect(
            f"file:{resolved.as_posix()}?mode=rwc",
            uri=True,
            timeout=30,
        )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _scalar(connection: sqlite3.Connection, sql: str, parameters=()):
    return connection.execute(sql, parameters).fetchone()[0]


def _backup_database(source_path: Path, backup_path: Path) -> None:
    with _connect(source_path, readonly=True) as source:
        with _connect(backup_path) as target:
            source.backup(target)

    with _connect(backup_path, readonly=True) as backup:
        result = _scalar(backup, "PRAGMA integrity_check")
        if result != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")


def _expected_counts(connection: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "media": """
            SELECT (SELECT COUNT(*) FROM legacy.movie)
                 + (SELECT COUNT(*) FROM legacy.tv_series)
                 + (SELECT COUNT(*) FROM legacy.tv_episode)
        """,
        "tmdb_metadata": """
            SELECT (SELECT COUNT(*) FROM legacy.movie)
                 + (SELECT COUNT(*) FROM legacy.tv_series)
                 + (SELECT COUNT(*) FROM legacy.tv_episode)
        """,
        "media_image": """
            SELECT (SELECT COUNT(*) FROM legacy.movie_image)
                 + (SELECT COUNT(*) FROM legacy.tv_series_image)
                 + (SELECT COUNT(*) FROM legacy.tv_episode_image)
        """,
        "media_source": """
            SELECT (SELECT COUNT(*) FROM legacy.movie_source)
                 + (SELECT COUNT(*) FROM legacy.tv_episode_source)
        """,
        "watch_progress": """
            SELECT (SELECT COUNT(*) FROM legacy.movie_watch_progress)
                 + (SELECT COUNT(*) FROM legacy.tv_episode_watch_progress)
        """,
        "favorite_folder": "SELECT COUNT(*) FROM legacy.favorite_folder",
        "favorite_item": "SELECT COUNT(*) FROM legacy.favorite_item",
    }
    return {name: _scalar(connection, sql) for name, sql in queries.items()}


def _validate_database(
    connection: sqlite3.Connection,
    expected: dict[str, int],
) -> dict:
    actual = {
        table: _scalar(connection, f'SELECT COUNT(*) FROM "{table}"')
        for table in expected
    }
    if actual != expected:
        raise RuntimeError(f"Row-count mismatch: expected={expected}, actual={actual}")

    checks = {
        "integrity_check": _scalar(connection, "PRAGMA main.integrity_check"),
        "foreign_key_errors": len(
            connection.execute("PRAGMA main.foreign_key_check").fetchall()
        ),
        "legacy_source_paths_missing": _scalar(
            connection,
            """
            SELECT COUNT(*)
              FROM (
                       SELECT alist_path FROM legacy.movie_source
                       UNION
                       SELECT alist_path FROM legacy.tv_episode_source
                       EXCEPT
                       SELECT alist_path FROM media_source
                   )
            """,
        ),
        "unexpected_source_paths": _scalar(
            connection,
            """
            SELECT COUNT(*)
              FROM (
                       SELECT alist_path FROM media_source
                       EXCEPT
                       SELECT alist_path FROM legacy.movie_source
                       EXCEPT
                       SELECT alist_path FROM legacy.tv_episode_source
                   )
            """,
        ),
        "legacy_image_paths_missing": _scalar(
            connection,
            """
            SELECT COUNT(*)
              FROM (
                       SELECT local_path FROM legacy.movie_image
                       UNION
                       SELECT local_path FROM legacy.tv_series_image
                       UNION
                       SELECT local_path FROM legacy.tv_episode_image
                       EXCEPT
                       SELECT local_path FROM media_image
                   )
            """,
        ),
        "unexpected_image_paths": _scalar(
            connection,
            """
            SELECT COUNT(*)
              FROM (
                       SELECT local_path FROM media_image
                       EXCEPT
                       SELECT local_path FROM legacy.movie_image
                       EXCEPT
                       SELECT local_path FROM legacy.tv_series_image
                       EXCEPT
                       SELECT local_path FROM legacy.tv_episode_image
                   )
            """,
        ),
        "matched_imdb_ids_missing": _scalar(
            connection,
            """
            SELECT COUNT(*)
              FROM media AS local
              LEFT JOIN imdb.title_basics AS title
                ON title.tconst = local.imdb_id
             WHERE local.match_status = 'matched'
               AND title.tconst IS NULL
            """,
        ),
        "matched_episode_hierarchy_errors": _scalar(
            connection,
            """
            SELECT COUNT(*)
              FROM media AS episode
              JOIN media AS series
                ON series.media_id = episode.parent_media_id
              LEFT JOIN imdb.title_episode AS mapping
                ON mapping.tconst = episode.imdb_id
             WHERE episode.media_type = 'episode'
               AND episode.match_status = 'matched'
               AND (
                       mapping.tconst IS NULL
                       OR mapping.parentTconst <> series.imdb_id
                       OR mapping.seasonNumber <> episode.season_number
                       OR mapping.episodeNumber <> episode.episode_number
                   )
            """,
        ),
    }
    if checks["integrity_check"] != "ok":
        raise RuntimeError(f"Integrity check failed: {checks['integrity_check']}")
    for name, value in checks.items():
        if name != "integrity_check" and value != 0:
            raise RuntimeError(f"Validation failed: {name}={value}")

    media_breakdown = [
        dict(row)
        for row in connection.execute(
            """
            SELECT media_type,
                   match_status,
                   COUNT(*) AS item_count
              FROM media
             GROUP BY media_type, match_status
             ORDER BY media_type, match_status
            """
        )
    ]
    return {
        "expected_counts": expected,
        "actual_counts": actual,
        "media_breakdown": media_breakdown,
        "checks": checks,
    }


def build_database(
    source_path: Path,
    imdb_path: Path,
    target_path: Path,
    backup_path: Path,
) -> dict:
    for required in (source_path, imdb_path, SCHEMA_PATH, MIGRATION_PATH):
        if not required.is_file():
            raise FileNotFoundError(required)
    for output in (target_path, backup_path):
        if output.exists():
            raise FileExistsError(output)

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _backup_database(source_path, backup_path)

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
    with _connect(target_path) as target:
        target.executescript(schema_sql)
        target.execute(
            "ATTACH DATABASE ? AS legacy",
            (f"file:{source_path.resolve().as_posix()}?mode=ro",),
        )
        target.execute(
            "ATTACH DATABASE ? AS imdb",
            (f"file:{imdb_path.resolve().as_posix()}?mode=ro",),
        )
        expected = _expected_counts(target)
        target.executescript(migration_sql)
        validation = _validate_database(target, expected)
        target.execute("DETACH DATABASE imdb")
        target.execute("DETACH DATABASE legacy")

    return {
        "source": str(source_path.resolve()),
        "imdb": str(imdb_path.resolve()),
        "target": str(target_path.resolve()),
        "backup": str(backup_path.resolve()),
        "validation": validation,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT / "movie.db")
    parser.add_argument("--imdb", type=Path, default=PROJECT_ROOT / "imdb.db")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()

    stamp = _timestamp()
    target = args.target or PROJECT_ROOT / f"movie.imdb-primary-{stamp}.db"
    backup = args.backup or PROJECT_ROOT / "backups" / f"movie-pre-imdb-primary-{stamp}.db"
    report = build_database(args.source, args.imdb, target, backup)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
