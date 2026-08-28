# -*- coding: utf-8 -*-
"""Batch import AList video files into movie.db by TMDB id.

Reads import_manifest.json (entries with alist_path / media_type / tmdb_id /
season / episode) and reuses helpers + models, skipping files that already
have a stored source so the run is resumable.

Usage:
    python batch_import.py             # full import
    python batch_import.py --dry-run   # only report what would happen
"""
import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import helpers
import models

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "movie.db"
IMAGE_ROOT = BASE_DIR / "static" / "images"
MANIFEST_PATH = BASE_DIR / "import_manifest.json"
FAILURE_PATH = BASE_DIR / "import_failures.json"

TMDB_DELAY_SECONDS = 0.35
PROGRESS_EVERY = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_import")


class BatchImporter:
    def __init__(self):
        self.series_details = {}
        self.season_details = {}
        self.counts = Counter()
        self.failures = []

    # ---------- TMDB caches ----------

    def get_series_details(self, series_id: int):
        if series_id not in self.series_details:
            self.series_details[series_id] = helpers.get_media_details(
                "tv",
                series_id,
            )
            time.sleep(TMDB_DELAY_SECONDS)

        return self.series_details[series_id]

    def get_season_details(self, series_id: int, season_number: int):
        key = (series_id, season_number)
        if key not in self.season_details:
            self.season_details[key] = helpers.search_tv_season(
                series_id,
                season_number,
            )
            time.sleep(TMDB_DELAY_SECONDS)

        return self.season_details[key]

    # ---------- import paths ----------

    def import_movie(self, entry: dict, dry_run: bool):
        movie_id = entry["tmdb_id"]
        if models.has_movie(DATABASE_PATH, movie_id):
            if dry_run:
                self.counts["movie_extra_source"] += 1
                return
            models.add_movie_source(
                DATABASE_PATH,
                movie_id,
                entry["alist_path"],
            )
            self.counts["movie_extra_source"] += 1
            return

        if dry_run:
            self.counts["movie_new"] += 1
            return

        details = helpers.get_media_details("movie", movie_id)
        time.sleep(TMDB_DELAY_SECONDS)
        images = helpers.download_media_images(
            "movie",
            movie_id,
            helpers.search_media_images("movie", movie_id),
            IMAGE_ROOT,
        )
        models.save_movie(DATABASE_PATH, details, images, entry["alist_path"])
        self.counts["movie_new"] += 1

    def import_episode(self, entry: dict, dry_run: bool):
        series_id = entry["tmdb_id"]
        season_number = entry["season"]
        episode_number = entry["episode"]

        episode_id = models.find_tv_episode_id(
            DATABASE_PATH,
            series_id,
            season_number,
            episode_number,
        )
        if episode_id is not None:
            if dry_run:
                self.counts["episode_extra_source"] += 1
                return
            models.add_tv_episode_source(
                DATABASE_PATH,
                episode_id,
                entry["alist_path"],
            )
            self.counts["episode_extra_source"] += 1
            return

        if dry_run:
            self.counts["episode_new"] += 1
            return

        series_details = self.get_series_details(series_id)
        season_details = self.get_season_details(series_id, season_number)
        if int(season_details.get("season_number", -1)) != season_number:
            raise ValueError("TMDB 季数据返回异常。")

        episode = helpers.get_tv_episode(season_details, episode_number)
        series_images = None
        if not models.get_tv_series_images(DATABASE_PATH, series_id):
            series_images = helpers.download_media_images(
                "tv",
                series_id,
                helpers.search_media_images("tv", series_id),
                IMAGE_ROOT,
            )
            time.sleep(TMDB_DELAY_SECONDS)
        episode_still = helpers.download_tv_episode_still(
            series_id,
            season_number,
            episode,
            IMAGE_ROOT,
        )
        models.save_tv_episode(
            DATABASE_PATH,
            series_details,
            season_details,
            episode_number,
            entry["alist_path"],
            series_images,
            episode_still,
        )
        self.counts["episode_new"] += 1

    # ---------- main loop ----------

    def run(self, entries: list, dry_run: bool):
        existing_paths = models.get_source_paths(DATABASE_PATH)
        total = len(entries)

        for index, entry in enumerate(entries, 1):
            if entry["alist_path"] in existing_paths:
                self.counts["skipped_existing"] += 1
                continue

            try:
                if entry["media_type"] == "movie":
                    self.import_movie(entry, dry_run)
                else:
                    self.import_episode(entry, dry_run)
            except Exception as error:
                self.counts["failed"] += 1
                self.failures.append(
                    {
                        "alist_path": entry["alist_path"],
                        "title": entry.get("title", ""),
                        "tmdb_id": entry["tmdb_id"],
                        "season": entry.get("season"),
                        "episode": entry.get("episode"),
                        "error": f"{type(error).__name__}: {error}",
                    },
                )
                logger.warning(
                    "导入失败 %s S%sE%s: %s",
                    entry.get("title", ""),
                    entry.get("season", "-"),
                    entry.get("episode", "-"),
                    error,
                )

            if index % PROGRESS_EVERY == 0:
                logger.info(
                    "进度 %d/%d (%s)",
                    index,
                    total,
                    dict(self.counts),
                )

        return self.counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        sys.exit(f"找不到导入清单: {MANIFEST_PATH}")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = manifest.get("entries", [])
    logger.info(
        "清单条目 %d（电影 %d / 剧集单集 %d）%s",
        len(entries),
        sum(1 for e in entries if e["media_type"] == "movie"),
        sum(1 for e in entries if e["media_type"] == "tv"),
        "【演练模式】" if args.dry_run else "",
    )

    importer = BatchImporter()
    counts = importer.run(entries, args.dry_run)

    print("\n===== 导入结果 =====")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")

    if importer.failures:
        FAILURE_PATH.write_text(
            json.dumps(importer.failures, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n失败 {len(importer.failures)} 条，明细见 {FAILURE_PATH.name}")
    else:
        print("\n全部条目处理成功。")


if __name__ == "__main__":
    main()
