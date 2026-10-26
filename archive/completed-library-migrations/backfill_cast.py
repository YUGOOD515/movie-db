from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from connectors import tmdb
from models.database import connect_database
from models.library import replace_library_cast


DATABASE_PATH = PROJECT_ROOT / "library.db"
IMAGE_ROOT = PROJECT_ROOT / "static" / "images"


def backfill_cast(database_path: Path, image_root: Path) -> dict:
    with closing(connect_database(database_path)) as db:
        titles = db.execute(
            """
            SELECT title_id, tmdb_id, title_type
              FROM title_basic
             WHERE title_type IN ('movie', 'series')
               AND tmdb_id IS NOT NULL
             ORDER BY title_id
            """
        ).fetchall()

    result = {"total": len(titles), "updated": 0, "failed": 0, "cast": 0}
    for title in titles:
        try:
            cast = tmdb.fetch_title_cast(
                title["title_id"],
                "tv" if title["title_type"] == "series" else "movie",
                title["tmdb_id"],
            )
            tmdb.download_metadata_images({"title_cast": cast}, image_root)
            for person in cast:
                local_path = person.get("local_path")
                if local_path and not (
                    image_root / Path(local_path).relative_to("images")
                ).is_file():
                    person["local_path"] = None
            replace_library_cast(database_path, title["title_id"], cast)
            result["updated"] += 1
            result["cast"] += len(cast)
        except (OSError, RuntimeError, ValueError, requests.RequestException, sqlite3.Error) as error:
            result["failed"] += 1
            print(f"{title['title_id']}: {error}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="回填电影与剧集的 TMDB 演员资料。")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--image-root", type=Path, default=IMAGE_ROOT)
    args = parser.parse_args()

    result = backfill_cast(args.database, args.image_root)
    print(
        f"完成：{result['updated']}/{result['total']} 个标题，"
        f"{result['cast']} 位演员，{result['failed']} 个失败。"
    )
    return int(result["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
