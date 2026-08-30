"""Populate library.db from movie.db, imdb.db and the TMDB API.

Usage:
    uv run python scripts/populate_library.py --test
    uv run python scripts/populate_library.py --all
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import helpers  # noqa: E402

MOVIE_DB = PROJECT_ROOT / "movie.db"
IMDB_DB = PROJECT_ROOT / "imdb.db"
LIBRARY_DB = PROJECT_ROOT / "library.db"

IMDB_TYPE_MAP = {
    "movie": "movie",
    "tvSeries": "series",
    "tvEpisode": "episode",
    "tvMiniSeries": "series",
}

IMAGE_TYPE_MAP = {
    "poster": "poster",
    "backdrop": "landscape_poster",
    "logo": "logo",
    "still": "still",
}

TEST_MOVIE_IDS = (1, 2)
TEST_SERIES_IDS = (10,)
TEST_EPISODE_LIMIT = 3

TMDB_REQUEST_DELAY = 0.25
TMDB_MAX_ATTEMPTS = 4
TMDB_RETRY_DELAY = 2.0


def _tmdb_with_retry(func, *args):
    last_error = None
    for attempt in range(1, TMDB_MAX_ATTEMPTS + 1):
        try:
            return func(*args)
        except Exception as error:  # noqa: BLE001 - network errors vary
            last_error = error
            wait = TMDB_RETRY_DELAY * attempt
            print(f"  [retry] TMDB request failed ({attempt}/{TMDB_MAX_ATTEMPTS}): {error}")
            time.sleep(wait)
    raise last_error


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    resolved = path.resolve()
    mode = "ro" if readonly else "rwc"
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode={mode}",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    if not readonly:
        connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _clean(value):
    """Normalize IMDb '\\N' placeholders to NULL."""
    if value is None or value == "\\N":
        return None
    return value


def _imdb_basic(imdb_conn: sqlite3.Connection, tconst: str):
    row = imdb_conn.execute(
        """
        SELECT b.primaryTitle, b.originalTitle, b.isAdult,
               b.startYear, b.endYear, b.runtimeMinutes, b.genres,
               r.averageRating, r.numVotes
        FROM title_basics b
        LEFT JOIN title_ratings r ON r.tconst = b.tconst
        WHERE b.tconst = ?
        """,
        (tconst,),
    ).fetchone()
    return row


class TmdbEnricher:
    """Fetch original_language / average_rating / num_votes from TMDB."""

    def __init__(self):
        self._details_cache = {}
        self._season_cache = {}

    def for_movie_or_series(self, media_type: str, tmdb_id: int):
        key = (media_type, tmdb_id)
        if key in self._details_cache:
            return self._details_cache[key]

        endpoint = "movie" if media_type == "movie" else "tv"
        details = _tmdb_with_retry(helpers.get_media_details, endpoint, tmdb_id)
        result = {
            "original_language": details.get("original_language"),
            "average_rating": details.get("vote_average"),
            "num_votes": details.get("vote_count"),
        }
        self._details_cache[key] = result
        time.sleep(TMDB_REQUEST_DELAY)
        return result

    def for_episode(self, series_tmdb_id: int, season_number: int,
                    episode_number: int, series_language: str | None):
        key = (series_tmdb_id, season_number)
        if key not in self._season_cache:
            season = _tmdb_with_retry(
                helpers.search_tv_season, series_tmdb_id, season_number
            )
            episodes = {
                int(item.get("episode_number", -1)): item
                for item in season.get("episodes", [])
            }
            self._season_cache[key] = episodes
            time.sleep(TMDB_REQUEST_DELAY)

        episode = self._season_cache[key].get(episode_number)
        if not episode:
            return {
                "original_language": series_language,
                "average_rating": None,
                "num_votes": None,
            }

        return {
            "original_language": series_language,
            "average_rating": episode.get("vote_average"),
            "num_votes": episode.get("vote_count"),
        }


def _build_title_row(media_row, imdb_row, tmdb_meta, enrich, created_at, updated_at):
    tconst = media_row["imdb_id"]
    media_type = media_row["media_type"]

    if imdb_row is None:
        raise ValueError(f"{tconst} not found in imdb.db")

    return (
        tconst,
        media_type,
        _clean(imdb_row["primaryTitle"]),
        _clean(imdb_row["originalTitle"]),
        enrich["original_language"],
        media_row["display_title"],
        int(_clean(imdb_row["isAdult"]) or 0),
        _clean(imdb_row["startYear"]),
        _clean(imdb_row["endYear"]),
        _clean(imdb_row["runtimeMinutes"]),
        _clean(imdb_row["genres"]),
        tmdb_meta["overview"] if tmdb_meta else None,
        tmdb_meta["tagline"] if tmdb_meta else None,
        enrich["average_rating"],
        enrich["num_votes"],
        media_row["tmdb_id"],
        created_at,
        updated_at,
    )


TITLE_INSERT_SQL = """
INSERT INTO title_basic (
    title_id, title_type, primary_title, original_title, original_language,
    display_title, is_adult, start_year, end_year, runtime_minutes, genres,
    overview, tagline, average_rating, num_votes, tmdb_id,
    added_at, updated_at
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
ON CONFLICT (title_id) DO UPDATE SET
    title_type = excluded.title_type,
    primary_title = excluded.primary_title,
    original_title = excluded.original_title,
    original_language = excluded.original_language,
    display_title = excluded.display_title,
    is_adult = excluded.is_adult,
    start_year = excluded.start_year,
    end_year = excluded.end_year,
    runtime_minutes = excluded.runtime_minutes,
    genres = excluded.genres,
    overview = excluded.overview,
    tagline = excluded.tagline,
    average_rating = excluded.average_rating,
    num_votes = excluded.num_votes,
    tmdb_id = excluded.tmdb_id,
    updated_at = excluded.updated_at
"""


def _migrate_titles(media_rows, movie_conn, imdb_conn, library_conn, enricher):
    """Insert title_basic rows (and gather episode relations)."""
    relations = []
    for media_row in media_rows:
        tconst = media_row["imdb_id"]
        if not tconst:
            continue

        imdb_row = _imdb_basic(imdb_conn, tconst)
        if imdb_row is None:
            print(f"  [skip] {tconst} not found in imdb.db")
            continue

        tmdb_meta = movie_conn.execute(
            "SELECT overview, tagline FROM tmdb_metadata WHERE media_id = ?",
            (media_row["media_id"],),
        ).fetchone()

        if media_row["media_type"] == "episode":
            parent = movie_conn.execute(
                """
                SELECT imdb_id, tmdb_id FROM media
                WHERE media_id = ?
                """,
                (media_row["parent_media_id"],),
            ).fetchone()
            if not parent or not parent["imdb_id"]:
                print(f"  [skip] episode {tconst} has no matched parent series")
                continue

            series_enrich = enricher.for_movie_or_series(
                "series", parent["tmdb_id"]
            )
            enrich = enricher.for_episode(
                parent["tmdb_id"],
                media_row["season_number"],
                media_row["episode_number"],
                series_enrich["original_language"],
            )
            relations.append(
                (tconst, parent["imdb_id"],
                 media_row["season_number"], media_row["episode_number"])
            )
        else:
            enrich = enricher.for_movie_or_series(
                media_row["media_type"], media_row["tmdb_id"]
            )

        library_conn.execute(
            TITLE_INSERT_SQL,
            _build_title_row(
                media_row, imdb_row, tmdb_meta, enrich,
                media_row["created_at"], media_row["updated_at"],
            ),
        )
        print(
            f"  [ok] {tconst} {media_row['media_type']:<7} "
            f"{media_row['display_title']}  "
            f"tmdb_lang={enrich['original_language']} "
            f"rating={enrich['average_rating']} votes={enrich['num_votes']}"
        )

    for episode_id, series_id, season_number, episode_number in relations:
        library_conn.execute(
            """
            INSERT INTO title_relation
                (episode_id, series_id, season_number, episode_number)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (episode_id) DO UPDATE SET
                series_id = excluded.series_id,
                season_number = excluded.season_number,
                episode_number = excluded.episode_number
            """,
            (episode_id, series_id, season_number, episode_number),
        )
    return relations


def _migrate_artwork(media_rows, movie_conn, library_conn):
    count = 0
    for media_row in media_rows:
        tconst = media_row["imdb_id"]
        if not tconst:
            continue
        rows = movie_conn.execute(
            """
            SELECT image_type, image_position, remote_path, local_path
            FROM media_image WHERE media_id = ?
            """,
            (media_row["media_id"],),
        ).fetchall()
        for image in rows:
            library_conn.execute(
                """
                INSERT INTO title_artwork
                    (title_id, artwork_type, position, remote_path, local_path)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (title_id, artwork_type, position) DO UPDATE SET
                    remote_path = excluded.remote_path,
                    local_path = excluded.local_path
                """,
                (
                    tconst,
                    IMAGE_TYPE_MAP.get(image["image_type"], image["image_type"]),
                    image["image_position"],
                    image["remote_path"],
                    image["local_path"],
                ),
            )
            count += 1
    return count


def _migrate_media_files(media_rows, movie_conn, library_conn):
    count = 0
    for media_row in media_rows:
        tconst = media_row["imdb_id"]
        if not tconst:
            continue
        rows = movie_conn.execute(
            """
            SELECT source_id, alist_path, media_duration_ms, file_size_bytes,
                   bit_rate, container_format, video_codec, video_width,
                   video_height, video_frame_rate, video_pixel_format,
                   video_dynamic_range, audio_codec, audio_channels,
                   audio_channel_layout, probed_at
            FROM media_source WHERE media_id = ?
            """,
            (media_row["media_id"],),
        ).fetchall()
        for source in rows:
            library_conn.execute(
                """
                INSERT INTO media_file (
                    file_id, title_id, alist_path, duration_ms,
                    file_size_bytes, bit_rate, container_format, video_codec,
                    video_width, video_height, video_frame_rate,
                    video_pixel_format, video_dynamic_range, audio_codec,
                    audio_channels, audio_channel_layout, probed_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (file_id) DO UPDATE SET
                    title_id = excluded.title_id,
                    alist_path = excluded.alist_path
                """,
                (
                    source["source_id"], tconst, source["alist_path"],
                    source["media_duration_ms"], source["file_size_bytes"],
                    source["bit_rate"], source["container_format"],
                    source["video_codec"], source["video_width"],
                    source["video_height"], source["video_frame_rate"],
                    source["video_pixel_format"],
                    source["video_dynamic_range"], source["audio_codec"],
                    source["audio_channels"],
                    source["audio_channel_layout"], source["probed_at"],
                ),
            )
            count += 1
    return count


def _migrate_playback(media_rows, movie_conn, library_conn):
    """watch_progress: one row per media; map to its lowest source_id file."""
    count = 0
    for media_row in media_rows:
        progress = movie_conn.execute(
            """
            SELECT position_ms, updated_at FROM watch_progress
            WHERE media_id = ?
            """,
            (media_row["media_id"],),
        ).fetchone()
        if not progress:
            continue

        tconst = media_row["imdb_id"]
        if not tconst:
            continue
        source = movie_conn.execute(
            """
            SELECT source_id FROM media_source WHERE media_id = ?
            ORDER BY source_id LIMIT 1
            """,
            (media_row["media_id"],),
        ).fetchone()
        if not source:
            print(f"  [skip] progress for {tconst} has no media_source")
            continue

        existing = library_conn.execute(
            """
            SELECT 1 FROM playback_progress
            WHERE file_id = ? AND watched_at = ?
            """,
            (source["source_id"], progress["updated_at"]),
        ).fetchone()
        if existing:
            continue

        library_conn.execute(
            """
            INSERT INTO playback_progress (file_id, position_ms, watched_at)
            VALUES (?, ?, ?)
            """,
            (source["source_id"], progress["position_ms"],
             progress["updated_at"]),
        )
        count += 1
    return count


def _migrate_collections(movie_conn, library_conn):
    count = 0
    for folder in movie_conn.execute(
        """
        SELECT folder_id, folder_name, folder_type, icon_name,
               folder_position, is_system, created_at
        FROM favorite_folder
        """
    ).fetchall():
        # folder_type: movie / series 限定类型；custom 等其他值表示不限类型。
        title_type = folder["folder_type"] \
            if folder["folder_type"] in ("movie", "series") else None
        library_conn.execute(
            """
            INSERT INTO collection (
                collection_id, name, collection_type, title_type,
                icon_name, "order", created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (collection_id) DO UPDATE SET
                name = excluded.name,
                collection_type = excluded.collection_type,
                title_type = excluded.title_type,
                icon_name = excluded.icon_name,
                "order" = excluded."order"
            """,
            (
                folder["folder_id"], folder["folder_name"],
                "system" if folder["is_system"] else "custom",
                title_type, folder["icon_name"],
                folder["folder_position"], folder["created_at"],
            ),
        )
        count += 1

    for item in movie_conn.execute(
        """
        SELECT folder_id, media_id, added_at FROM favorite_item
        """
    ).fetchall():
        media = movie_conn.execute(
            "SELECT imdb_id FROM media WHERE media_id = ?",
            (item["media_id"],),
        ).fetchone()
        if not media or not media["imdb_id"]:
            continue
        library_conn.execute(
            """
            INSERT INTO collection_item (collection_id, title_id, added_at)
            VALUES (?, ?, ?)
            ON CONFLICT (collection_id, title_id) DO UPDATE SET
                added_at = excluded.added_at
            """,
            (item["folder_id"], media["imdb_id"], item["added_at"]),
        )
        count += 1
    return count


def _load_media_rows(movie_conn, *, test: bool):
    if not test:
        return movie_conn.execute(
            """
            SELECT * FROM media
            WHERE imdb_id IS NOT NULL
            ORDER BY media_type, media_id
            """
        ).fetchall()

    movies = movie_conn.execute(
        "SELECT * FROM media WHERE media_id IN (%s) ORDER BY media_id"
        % ",".join(str(i) for i in TEST_MOVIE_IDS)
    ).fetchall()
    series = movie_conn.execute(
        "SELECT * FROM media WHERE media_id IN (%s) ORDER BY media_id"
        % ",".join(str(i) for i in TEST_SERIES_IDS)
    ).fetchall()
    episodes = []
    for row in series:
        episodes.extend(movie_conn.execute(
            """
            SELECT * FROM media
            WHERE parent_media_id = ? AND imdb_id IS NOT NULL
            ORDER BY season_number, episode_number
            LIMIT ?
            """,
            (row["media_id"], TEST_EPISODE_LIMIT),
        ).fetchall())
    return list(movies) + list(series) + episodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true",
                       help="migrate a small fixed sample")
    group.add_argument("--all", action="store_true",
                       help="migrate every matched title")
    args = parser.parse_args()

    with _connect(MOVIE_DB, readonly=True) as movie_conn, \
            _connect(IMDB_DB, readonly=True) as imdb_conn, \
            _connect(LIBRARY_DB) as library_conn:
        media_rows = _load_media_rows(movie_conn, test=args.test)
        total = len(media_rows)
        print(f"Selected {total} media rows to migrate")

        enricher = TmdbEnricher()
        with library_conn:
            print("1. title_basic + title_relation")
            relations = _migrate_titles(
                media_rows, movie_conn, imdb_conn, library_conn, enricher
            )
            print(f"   relations inserted: {len(relations)}")

            print("2. title_artwork")
            artwork_count = _migrate_artwork(media_rows, movie_conn, library_conn)
            print(f"   rows: {artwork_count}")

            print("3. media_file")
            file_count = _migrate_media_files(media_rows, movie_conn, library_conn)
            print(f"   rows: {file_count}")

            print("4. playback_progress")
            progress_count = _migrate_playback(media_rows, movie_conn, library_conn)
            print(f"   rows: {progress_count}")

            print("5. collection / collection_item")
            collection_count = _migrate_collections(movie_conn, library_conn)
            print(f"   rows: {collection_count}")

        result = library_conn.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"integrity_check: {result}")
        for table in ("title_basic", "title_relation", "title_artwork",
                      "media_file", "playback_progress",
                      "collection", "collection_item"):
            n = library_conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            print(f"  {table}: {n}")


if __name__ == "__main__":
    main()
