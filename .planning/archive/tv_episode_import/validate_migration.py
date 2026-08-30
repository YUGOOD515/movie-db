import sqlite3
from pathlib import Path


root = Path(__file__).resolve().parents[2]
source = sqlite3.connect(
    root
    / "backups"
    / "movie-before-tv-20260827-220057-sqlite.db"
)
database = sqlite3.connect(":memory:")
source.backup(database)
source.close()

database.executescript(
    (root / "migrations" / "002_add_tv_episode_support.sql").read_text(
        encoding="utf-8"
    )
)
tables = database.execute(
    """
    SELECT COUNT(*)
      FROM sqlite_master
     WHERE type = 'table'
           AND name LIKE 'tv_%'
    """
).fetchone()[0]
indexes = database.execute(
    """
    SELECT COUNT(*)
      FROM sqlite_master
     WHERE type = 'index'
           AND name LIKE 'tv_%'
    """
).fetchone()[0]

print("tables=", tables)
print("indexes=", indexes)
print("integrity=", database.execute("PRAGMA integrity_check").fetchone()[0])
print("foreign_keys=", database.execute("PRAGMA foreign_key_check").fetchall())
database.close()

fresh_database = sqlite3.connect(":memory:")
fresh_database.executescript(
    (root / "migrations" / "schema.sql").read_text(encoding="utf-8")
)
fresh_tables = fresh_database.execute(
    """
    SELECT COUNT(*)
      FROM sqlite_master
     WHERE type = 'table'
           AND name LIKE 'tv_%'
    """
).fetchone()[0]
print("fresh_tables=", fresh_tables)
print(
    "fresh_integrity=",
    fresh_database.execute("PRAGMA integrity_check").fetchone()[0],
)
print(
    "fresh_foreign_keys=",
    fresh_database.execute("PRAGMA foreign_key_check").fetchall(),
)
fresh_database.close()
