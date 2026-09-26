import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import import_imdb


class CompactImdbImportTests(unittest.TestCase):
    def test_full_alias_tsv_projects_columns_and_preserves_chinese_selection(self):
        table = next(t for t in import_imdb.TABLES if t["name"] == "title_akas")
        header = "titleId\tordering\ttitle\tregion\tlanguage\ttypes\tattributes\tisOriginalTitle\n"
        rows = [
            "tt1\t1\tEnglish\tUS\ten\timdbDisplay\t\\N\t0\n",
            "tt1\t2\t中文\tCN\t\\N\timdbDisplay\t\\N\t0\n",
            "tt1\t3\t繁體\tTW\tzh\t\\N\t\\N\t0\n",
            "tt1\t4\t中文別名\t\\N\tzh\t\\N\t\\N\t0\n",
            "tt1\t5\tNot selected\tMO\ten\t\\N\t\\N\t0\n",
            "tt1\t6\tNot selected\tUS\tzh-Hans\t\\N\t\\N\t0\n",
            "malformed\trow\n",
        ]
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(":memory:")) as db:
            root = Path(directory)
            (root / table["file"]).write_text(header + "".join(rows), encoding="utf-8")
            with patch.object(import_imdb, "IMDB_DIR", root):
                result = import_imdb.import_table(db, table)
            self.assertEqual(result, {"scanned": 7, "kept": 3, "bad": 1})
            self.assertEqual(db.execute("SELECT * FROM title_akas ORDER BY ordering").fetchall(), [
                ("tt1", 2, "中文", "CN", None),
                ("tt1", 3, "繁體", "TW", "zh"),
                ("tt1", 4, "中文別名", None, "zh"),
            ])

    def test_missing_source_column_does_not_drop_existing_table(self):
        table = next(t for t in import_imdb.TABLES if t["name"] == "title_ratings")
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(":memory:")) as db:
            root = Path(directory)
            (root / table["file"]).write_text("tconst\taverageRating\ntt1\t8.0\n", encoding="utf-8")
            db.execute(import_imdb.build_create_sql(table))
            db.execute("INSERT INTO title_ratings VALUES ('tt1', 9.0, 100)")
            db.commit()
            with patch.object(import_imdb, "IMDB_DIR", root), self.assertRaises(ValueError):
                import_imdb.import_table(db, table)
            self.assertEqual(db.execute("SELECT * FROM title_ratings").fetchall(), [("tt1", 9.0, 100)])


if __name__ == "__main__":
    unittest.main()
