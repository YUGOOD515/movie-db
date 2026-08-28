import sqlite3
import sys
import tempfile
from pathlib import Path


root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))

import helpers  # noqa: E402
import models  # noqa: E402


series_id = int(helpers.search_tv_series("绝命毒师")["results"][0]["id"])
series_details = helpers.search_tv_series_details(series_id)
season_details = helpers.search_tv_season(series_id, 1)
episode_one = helpers.get_tv_episode(season_details, 1)
episode_two = helpers.get_tv_episode(season_details, 2)
image_links = helpers.search_tv_img(series_id)
print("candidates=", {key: len(value) for key, value in image_links.items()})

with tempfile.TemporaryDirectory() as temporary_directory:
    temporary_root = Path(temporary_directory)
    image_root = temporary_root / "static" / "images"
    database_path = temporary_root / "tv-images.db"
    database = sqlite3.connect(database_path)
    database.executescript(
        (root / "migrations" / "schema.sql").read_text(encoding="utf-8")
    )
    database.close()

    series_images = helpers.download_tv_series_images(
        series_id,
        image_links,
        image_root,
    )
    still_one = helpers.download_tv_episode_still(
        series_id,
        1,
        episode_one,
        image_root,
    )
    models.save_tv_episode(
        database_path,
        series_details,
        season_details,
        1,
        "/Quark/live-test/Breaking.Bad.S01E01.mkv",
        series_images,
        still_one,
    )

    still_two = helpers.download_tv_episode_still(
        series_id,
        1,
        episode_two,
        image_root,
    )
    models.save_tv_episode(
        database_path,
        series_details,
        season_details,
        2,
        "/Quark/live-test/Breaking.Bad.S01E02.mkv",
        None,
        still_two,
    )

    database = sqlite3.connect(database_path)
    print("series_images=", database.execute("SELECT COUNT(*) FROM tv_series_image").fetchone()[0])
    print("episode_images=", database.execute("SELECT COUNT(*) FROM tv_episode_image").fetchone()[0])
    print("sources=", database.execute("SELECT COUNT(*) FROM tv_episode_source").fetchone()[0])
    print("integrity=", database.execute("PRAGMA integrity_check").fetchone()[0])
    print("foreign_keys=", database.execute("PRAGMA foreign_key_check").fetchall())
    database.close()
