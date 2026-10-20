import gzip
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from connectors import imdb_ratings, tmdb
from models.library import import_media_bundle
from services import rating_sync

ROOT = Path(__file__).resolve().parents[1]


def write_ratings(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as file:
        file.write("tconst\taverageRating\tnumVotes\n")
        for row in rows:
            file.write("\t".join(map(str, row)) + "\n")


class ImdbRatingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "title.ratings.tsv.gz"

    def test_lookup_streams_only_requested_titles(self):
        write_ratings(self.path, [("tt0000001", 5.7, 2100), ("tt0903747", 9.5, 2400000)])
        self.assertEqual(
            imdb_ratings.lookup_ratings(["tt0903747", "tt404"], self.path),
            {"tt0903747": (9.5, 2400000)},
        )
        self.assertEqual(imdb_ratings.lookup_ratings([], self.path), {})

    def test_fresh_file_skips_download_and_failed_refresh_keeps_old_file(self):
        write_ratings(self.path, [("tt1", 7.0, 10)])
        with patch.object(imdb_ratings.requests, "get") as get:
            self.assertEqual(imdb_ratings.ensure_ratings_file(self.path), self.path)
            get.assert_not_called()
        os.utime(self.path, (0, 0))
        with patch.object(imdb_ratings.requests, "get", side_effect=requests.Timeout("offline")):
            self.assertEqual(imdb_ratings.ensure_ratings_file(self.path), self.path)
        self.assertEqual(imdb_ratings.lookup_ratings(["tt1"], self.path), {"tt1": (7.0, 10)})
        missing = Path(self.directory.name) / "missing.tsv.gz"
        with patch.object(imdb_ratings.requests, "get", side_effect=requests.Timeout("offline")):
            self.assertIsNone(imdb_ratings.ensure_ratings_file(missing))
        self.assertFalse(missing.with_name(missing.name + ".part").exists())


def translations(*items):
    return {"translations": [
        {"iso_639_1": language, "iso_3166_1": region, "data": {"name": name, "title": name}}
        for language, region, name in items
    ]}


class TmdbTitleTest(unittest.TestCase):
    def setUp(self):
        tmdb._cache.clear()
        self.addCleanup(tmdb._cache.clear)

    def test_title_basic_uses_chinese_regions_english_title_and_imdb_genres(self):
        series = tmdb.build_title_basic({
            "id": 95396, "name": "Severance", "original_name": "Severance", "adult": False,
            "first_air_date": "2022-02-17", "last_air_date": "2025-03-20", "status": "Returning Series",
            "episode_run_time": [55], "original_language": "en",
            "genres": [{"id": 18}, {"id": 10765}, {"id": 9648}, {"id": 18}],
            "translations": translations(("zh", "TW", "人生切割術"), ("en", "US", "Severance")),
        }, "tt11280740", "series")
        self.assertEqual(series["display_title"], "人生切割術")
        self.assertEqual(series["primary_title"], "Severance")
        self.assertEqual(series["genres"], "Drama,Sci-Fi,Fantasy,Mystery")
        self.assertEqual((series["start_year"], series["end_year"], series["runtime_minutes"]), (2022, None, 55))
        movie = tmdb.build_title_basic({
            "id": 496243, "title": "寄生虫", "original_title": "기생충", "release_date": "2019-05-30",
            "runtime": 0, "genres": [{"id": 10770}],
            "translations": translations(("zh", "CN", "寄生虫"), ("en", "US", "Parasite")),
        }, "tt6751668", "movie")
        self.assertEqual((movie["display_title"], movie["primary_title"], movie["original_title"]), ("寄生虫", "Parasite", "기생충"))
        self.assertIsNone(movie["genres"])
        self.assertIsNone(movie["runtime_minutes"])
        ended = tmdb.build_title_basic({"id": 1, "name": "剧", "status": "Ended", "last_air_date": "2013-09-29"}, "tt1", "series")
        self.assertEqual(ended["end_year"], 2013)

    def test_episode_input_resolves_parent_series(self):
        tmdb._remember(("find", "tt15180436", tmdb.LANGUAGE),
                       ("episode", {"show_id": 95396, "season_number": 2, "episode_number": 1}))
        with patch.object(tmdb, "_get", return_value={"imdb_id": "tt11280740"}) as get:
            identity = tmdb.identify_title("tt15180436", session=Mock())
        self.assertEqual(identity, {"title_type": "episode", "root_id": "tt11280740",
                                    "season_number": 2, "episode_number": 1})
        self.assertEqual(get.call_args.args[1], "/tv/95396/external_ids")
        self.assertEqual(tmdb.find_media(Mock(), "tt11280740"), ("tv", {"id": 95396}))

    def test_series_reuses_known_ids_batches_new_ones_and_skips_conflicts(self):
        tmdb.remember_media_identity("tt0903747", "tv", 1396)
        episodes = [{"id": 100 + n, "season_number": 1, "episode_number": n, "name": f"E{n}",
                     "air_date": "2008-01-20", "runtime": 47} for n in range(1, 26)]
        requests_seen = []

        def detail(session, media_type, tmdb_id, append=""):
            requests_seen.append(append)
            if append.startswith("images"):
                return {"id": 1396, "name": "绝命毒师", "original_name": "Breaking Bad", "genres": [{"id": 80}],
                        "seasons": [{"season_number": 1, "episode_count": 25}]}
            if append == "season/1":
                return {"season/1": {"episodes": episodes}}
            payload = {}
            for key in append.split(","):
                number = int(key.split("/")[3])
                imdb_id = {2: "", 5: "tt0000001"}.get(number, f"tt99{number:05d}")
                payload[key] = {"imdb_id": imdb_id}
            return payload

        with patch.object(tmdb, "fetch_detail", side_effect=detail):
            metadata = tmdb.build_media_metadata("tt0903747", session=Mock(), known_episode_ids={(1, 1): "tt0000001"})
        id_requests = [append for append in requests_seen if "external_ids" in append]
        self.assertEqual([len(append.split(",")) for append in id_requests], [20, 4])
        self.assertNotIn("season/1/episode/1/external_ids", ",".join(id_requests))
        positions = {item["episode_number"]: item["title_basic"]["title_id"] for item in metadata["episodes"]}
        self.assertEqual(positions[1], "tt0000001")
        self.assertNotIn(2, positions)  # TMDB has no IMDb ID
        self.assertNotIn(5, positions)  # Conflicts with an existing relation
        self.assertEqual(len(positions), 23)
        first = metadata["episodes"][0]["title_basic"]
        self.assertEqual((first["title_type"], first["genres"], first["start_year"], first["runtime_minutes"]),
                         ("episode", "Crime", 2008, 47))
        # Cached copies are independent of caller mutations.
        metadata["title_basic"]["display_title"] = "changed"
        with patch.object(tmdb, "fetch_detail", side_effect=AssertionError("cached")):
            again = tmdb.build_media_metadata("tt0903747", session=Mock(), known_episode_ids={(1, 1): "tt0000001"})
        self.assertEqual(again["title_basic"]["display_title"], "绝命毒师")


class LibraryRatingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "library.db"
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript((ROOT / "migrations" / "library_schema.sql").read_text(encoding="utf-8"))

    def row(self, **values):
        base = {"title_id": "tt1", "tmdb_id": 1, "title_type": "series", "primary_title": "Old Name",
                "original_title": "Old Name", "original_language": "en", "display_title": "旧中文名",
                "is_adult": 0, "start_year": 2020, "end_year": None, "runtime_minutes": 50, "genres": "Drama",
                "overview": None, "tagline": None, "average_rating": 8.0, "num_votes": 100}
        return {**base, **values}

    def write(self, titles, path):
        import_media_bundle(titles=titles, relations=[], artwork=[], cast=None, target_title_id=titles[-1]["title_id"],
                            alist_path=path, probe={}, database_path=self.database)

    def test_reimport_keeps_names_genres_and_ratings_but_replaces_placeholders(self):
        self.write([self.row(), self.row(title_id="tt2", title_type="episode", display_title="第 2 集", primary_title="Episode #1.2")], "/a.mkv")
        self.write([
            self.row(display_title="新中文名", primary_title="New", genres="Crime", average_rating=None, num_votes=None, end_year=2024),
            self.row(title_id="tt2", title_type="episode", display_title="真正的集名", primary_title="真正的集名"),
        ], "/b.mkv")
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute("SELECT title_id, display_title, primary_title, genres, average_rating, num_votes, end_year FROM title_basic ORDER BY title_id").fetchall()
        self.assertEqual(rows, [("tt1", "旧中文名", "Old Name", "Drama", 8.0, 100, 2024),
                                ("tt2", "真正的集名", "真正的集名", "Drama", 8.0, 100, None)])

    def test_refresh_updates_every_title_from_the_ratings_file(self):
        self.write([self.row(), self.row(title_id="tt2", title_type="episode")], "/a.mkv")
        path = Path(self.directory.name) / "ratings.tsv.gz"
        write_ratings(path, [("tt1", 8.0, 100), ("tt2", 9.1, 321)])
        with patch.object(imdb_ratings, "ensure_ratings_file", return_value=path):
            self.assertEqual(rating_sync.refresh_imdb_ratings(self.database), 1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT average_rating, num_votes FROM title_basic WHERE title_id = 'tt2'").fetchone(), (9.1, 321))


if __name__ == "__main__":
    unittest.main()
