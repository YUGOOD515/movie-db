"""One-off read-only source rebuild; replacement is a separate verified step."""
import hashlib
from contextlib import closing
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.import_imdb import TABLES, build_create_sql
from queries.imdb import get_imdb_title, get_series_episodes

SOURCE = ROOT / "imdb.db"
TARGET = Path(__file__).with_name("imdb.compact.db")
REPORT = Path(__file__).with_name("verification.json")


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    if TARGET.exists():
        raise RuntimeError("Target already exists; inspect it before retrying.")
    if list(ROOT.glob("imdb.db-*")):
        raise RuntimeError("Source has SQLite sidecars; inspect active database state.")
    before = SOURCE.stat()
    report = {
        "source_bytes": before.st_size,
        "source_mtime_ns": before.st_mtime_ns,
        "library_sha256": file_hash(ROOT / "library.db"),
        "tables": {},
    }
    with closing(sqlite3.connect(TARGET, uri=True)) as dst:
        dst.execute("PRAGMA synchronous = FULL")
        dst.execute("PRAGMA cache_size = -64000")
        dst.execute("ATTACH DATABASE ? AS source", (SOURCE.as_uri() + "?mode=ro",))
        for table in TABLES:
            name = table["name"]
            columns = [column for column, _ in table["columns"]]
            where = "WHERE region IN ('CN', 'TW', 'HK') OR language = 'zh'" if name == "title_akas" else ""
            dst.execute(build_create_sql(table))
            dst.execute(f"INSERT INTO main.{name} SELECT {', '.join(columns)} FROM source.{name} {where}")
            for index, keys in table["indexes"].items():
                dst.execute(f"CREATE INDEX {index} ON {name} ({', '.join(keys)})")
            dst.commit()
            print(f"Copied {name}", flush=True)
            count = dst.execute(f"SELECT COUNT(*) FROM main.{name}").fetchone()[0]
            expected = dst.execute(f"SELECT COUNT(*) FROM source.{name} {where}").fetchone()[0]
            assert count == expected, (name, count, expected)
            join = " AND ".join(f"a.{key} = b.{key}" for key in table["primary_key"])
            same = " AND ".join(f"a.{column} IS b.{column}" for column in columns)
            selection = "(a.region IN ('CN', 'TW', 'HK') OR a.language = 'zh') AND " if where else ""
            mismatch = dst.execute(
                f"SELECT 1 FROM source.{name} a LEFT JOIN main.{name} b ON {join} "
                f"WHERE {selection}NOT ({same}) LIMIT 1"
            ).fetchone()
            assert mismatch is None, name
            report["tables"][name] = {"rows": count, "all_retained_values_equal": True}
            print(f"Verified all {count:,} rows: {name}", flush=True)
        dst.execute("ANALYZE main")
        dst.commit()
        integrity = dst.execute("PRAGMA main.integrity_check").fetchall()
        assert integrity == [("ok",)], integrity
        report["integrity_check"] = "ok"
    with closing(sqlite3.connect(SOURCE.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(TARGET.as_uri() + "?mode=ro", uri=True)) as dst, closing(sqlite3.connect((ROOT / "library.db").as_uri() + "?mode=ro", uri=True)) as library:
        src.row_factory = dst.row_factory = sqlite3.Row
        titles = library.execute("SELECT title_id, title_type FROM title_basic").fetchall()
        series = 0
        for title_id, title_type in titles:
            assert get_imdb_title(src, title_id) == get_imdb_title(dst, title_id), title_id
            if title_type == "series":
                assert get_series_episodes(src, title_id) == get_series_episodes(dst, title_id), title_id
                series += 1
        report["identical_title_queries"] = len(titles)
        report["identical_series_queries"] = series
    after = SOURCE.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    assert report["library_sha256"] == file_hash(ROOT / "library.db")
    report["target_bytes"] = TARGET.stat().st_size
    report["target_sha256"] = file_hash(TARGET)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
