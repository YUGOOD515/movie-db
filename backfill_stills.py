# -*- coding: utf-8 -*-
"""Backfill episode stills for episodes that already have sources."""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import helpers
import models

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "movie.db"
IMAGE_ROOT = BASE_DIR / "static" / "images"

TMDB_DELAY_SECONDS = 0.3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_stills")


def main():
    episodes = models.find_episodes_missing_stills(DATABASE_PATH)
    total = len(episodes)
    logger.info("需要补剧照的单集: %d", total)

    season_cache = {}
    saved = 0
    skipped = 0
    failed = 0

    for index, episode in enumerate(episodes, 1):
        series_id = episode["series_id"]
        season_number = episode["season_number"]
        key = (series_id, season_number)

        try:
            if key not in season_cache:
                season_cache[key] = helpers.search_tv_season(
                    series_id,
                    season_number,
                )
                time.sleep(TMDB_DELAY_SECONDS)

            episode_data = helpers.get_tv_episode(
                season_cache[key],
                episode["episode_number"],
            )
            still = helpers.download_tv_episode_still(
                series_id,
                season_number,
                episode_data,
                IMAGE_ROOT,
            )
            if still is None:
                skipped += 1
                continue

            models.upsert_tv_episode_still(
                DATABASE_PATH,
                still["episode_id"],
                still["local_path"],
            )
            saved += 1
        except Exception as error:
            failed += 1
            logger.warning(
                "剧照失败 %s S%dE%d: %s",
                series_id,
                season_number,
                episode["episode_number"],
                error,
            )

        if index % 50 == 0:
            logger.info(
                "进度 %d/%d (保存 %d / 无剧照 %d / 失败 %d)",
                index,
                total,
                saved,
                skipped,
                failed,
            )

    print("\n===== 剧照回填结果 =====")
    print(f"  保存: {saved}")
    print(f"  TMDB 无剧照: {skipped}")
    print(f"  失败: {failed}")


if __name__ == "__main__":
    main()
