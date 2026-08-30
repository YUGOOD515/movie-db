import sqlite3
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))

import helpers  # noqa: E402
import models  # noqa: E402


search_results = helpers.search_tv_series("绝命毒师").get("results", [])
series_id = int(search_results[0]["id"])
series_details = helpers.search_tv_series_details(series_id)
season_details = helpers.search_tv_season(series_id, 1)
episode = helpers.get_tv_episode(season_details, 1)

with tempfile.TemporaryDirectory() as temporary_directory:
    database_path = Path(temporary_directory) / "tv.db"
    database = sqlite3.connect(database_path)
    database.executescript(
        (root / "migrations" / "schema.sql").read_text(encoding="utf-8")
    )
    database.close()

    models.save_tv_episode(
        database_path,
        series_details,
        season_details,
        1,
        "/Quark/live-test/Breaking.Bad.S01E01.mkv",
    )

    database = sqlite3.connect(database_path)
    print("series=", database.execute("SELECT COUNT(*) FROM tv_series").fetchone()[0])
    print("seasons=", database.execute("SELECT COUNT(*) FROM tv_season").fetchone()[0])
    print("episodes=", database.execute("SELECT COUNT(*) FROM tv_episode").fetchone()[0])
    print("sources=", database.execute("SELECT COUNT(*) FROM tv_episode_source").fetchone()[0])
    print("selected_episode=", episode["id"], episode["name"])
    print("integrity=", database.execute("PRAGMA integrity_check").fetchone()[0])
    print("foreign_keys=", database.execute("PRAGMA foreign_key_check").fetchall())
    database.close()
