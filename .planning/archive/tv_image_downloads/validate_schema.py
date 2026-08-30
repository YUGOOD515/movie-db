import sqlite3
from pathlib import Path


root = Path(__file__).resolve().parents[2]
source = sqlite3.connect(
    root
    / "backups"
    / "movie-before-tv-images-20260827-222051-sqlite.db"
)
migrated = sqlite3.connect(":memory:")
source.backup(migrated)
source.close()
migrated.executescript(
    (root / "migrations" / "003_add_tv_images.sql").read_text(encoding="utf-8")
)

fresh = sqlite3.connect(":memory:")
fresh.executescript(
    (root / "migrations" / "schema.sql").read_text(encoding="utf-8")
)

for label, database in (("migration", migrated), ("fresh", fresh)):
    image_tables = database.execute(
        """
        SELECT COUNT(*)
          FROM sqlite_master
         WHERE type = 'table'
               AND name IN ('tv_series_image', 'tv_episode_image')
        """
    ).fetchone()[0]
    print(label, "image_tables=", image_tables)
    print(label, "integrity=", database.execute("PRAGMA integrity_check").fetchone()[0])
    print(label, "foreign_keys=", database.execute("PRAGMA foreign_key_check").fetchall())
    database.close()
