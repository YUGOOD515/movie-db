import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import app as movie_app
import helpers
import models


class ImdbPrimaryIntegrationTest(unittest.TestCase):
    def setUp(self):
        configured = os.environ.get("IMDB_PRIMARY_TEST_DATABASE")
        source = Path(configured) if configured else movie_app.BASE_DIR / "movie.db"
        if not source.is_file():
            self.skipTest("IMDb-primary database is not available")

        with closing(sqlite3.connect(source)) as connection:
            is_primary = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media'"
            ).fetchone()
        if not is_primary:
            self.skipTest("movie.db has not switched to the IMDb-primary schema")

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        with closing(sqlite3.connect(source)) as source_db:
            with closing(sqlite3.connect(self.database_path)) as target_db:
                source_db.backup(target_db)

        self.previous_database = movie_app.app.config["DATABASE"]
        movie_app.app.config.update(TESTING=True, DATABASE=self.database_path)
        self.client = movie_app.app.test_client()

    def tearDown(self):
        movie_app.app.config["DATABASE"] = self.previous_database
        self.temporary_directory.cleanup()

    def test_schema_and_imdb_links_are_valid(self):
        with closing(models._connect(self.database_path)) as database:
            tables = {
                row[0]
                for row in database.execute(
                    """
                    SELECT name
                      FROM main.sqlite_master
                     WHERE type = 'table'
                       AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
            self.assertEqual(
                tables,
                {
                    "favorite_folder",
                    "favorite_item",
                    "media",
                    "media_image",
                    "media_source",
                    "tmdb_metadata",
                    "watch_progress",
                },
            )
            self.assertGreater(database.execute("SELECT COUNT(*) FROM media").fetchone()[0], 0)
            self.assertEqual(
                database.execute(
                    """
                    SELECT COUNT(*)
                      FROM media AS local
                      LEFT JOIN imdb.title_basics AS title
                        ON title.tconst = local.imdb_id
                     WHERE local.match_status = 'matched'
                       AND title.tconst IS NULL
                    """
                ).fetchone()[0],
                0,
            )
            self.assertEqual(database.execute("PRAGMA main.integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA main.foreign_key_check").fetchall(), [])

    def test_core_routes_render_from_new_schema(self):
        for path in (
            "/",
            "/media",
            "/recent-added",
            "/unwatched",
            "/favorites",
            "/genres?media_type=movie&genre_id=7",
            "/top-rated",
            "/movies/157336",
            "/series/71728",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_rating_labels_match_imdb_primary_data(self):
        detail = self.client.get("/movies/157336").get_data(as_text=True)
        top_rated = self.client.get("/top-rated").get_data(as_text=True)
        filters = self.client.get("/genres").get_data(as_text=True)

        self.assertIn("IMDb", detail)
        self.assertNotIn("TMDB 评分", detail)
        self.assertNotIn("Rotten Tomatoes", detail)
        self.assertNotIn("Metacritic", detail)
        self.assertIn("按 IMDb 评分", top_rated)
        self.assertNotIn("制作国家", filters)

    def test_missing_media_and_favorite_folder_return_404(self):
        self.assertEqual(self.client.get("/movies/999999999").status_code, 404)
        self.assertEqual(self.client.get("/series/999999999").status_code, 404)
        self.assertEqual(self.client.get("/favorites?folder_id=999999999").status_code, 404)

    def test_progress_favorite_and_artwork_writes(self):
        models.save_watch_progress(
            self.database_path, "movie", 157336, 10_000, 100_000
        )
        progress = models.get_watch_progress(self.database_path, "movie", 157336)
        self.assertEqual(progress["position_ms"], 10_000)

        self.assertTrue(models.toggle_media_favorite(self.database_path, "movie", 157336))
        self.assertTrue(models.is_media_favorite(self.database_path, "movie", 157336))
        page = models.get_favorite_page_data(self.database_path, 1)
        self.assertEqual(page["items"][0]["media_id"], 157336)

        models.set_primary_media_image(
            self.database_path, "movie", 157336, "posters", 2
        )
        with closing(models._connect(self.database_path)) as database:
            self.assertEqual(database.execute("PRAGMA main.integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA main.foreign_key_check").fetchall(), [])

    def test_movie_write_requires_and_uses_local_imdb_identity(self):
        details = {
            "id": 999001,
            "imdb_id": "tt0111161",
            "title": "肖申克的救赎",
            "overview": "TMDB 补充简介",
            "tagline": "希望让人自由",
            "homepage": "https://example.test/movie",
            "status": "Released",
        }
        models.save_movie(
            self.database_path,
            details,
            [],
            "/Movies/Shawshank.mkv",
            {"video_codec": "hevc", "video_width": 3840, "video_height": 2160},
        )
        saved = models.get_media_detail(self.database_path, "movie", 999001)
        self.assertEqual(saved["imdb_id"], "tt0111161")
        self.assertEqual(saved["name"], "肖申克的救赎")
        self.assertEqual(saved["overview"], "TMDB 补充简介")
        self.assertEqual(saved["sources"][0]["video_codec"], "hevc")

        invalid = {"id": 999002, "title": "No IMDb identity"}
        with self.assertRaisesRegex(ValueError, "IMDb"):
            models.save_movie(self.database_path, invalid, [], "/Movies/invalid.mkv")

    def test_tv_write_uses_imdb_episode_hierarchy_without_season_table(self):
        series = {
            "id": 999100,
            "imdb_id": "tt0903747",
            "name": "绝命毒师",
            "original_language": "en",
            "overview": "TMDB 剧集简介",
            "status": "Ended",
        }
        season = {
            "season_number": 1,
            "episodes": [
                {
                    "id": 999101,
                    "episode_number": 1,
                    "name": "试播集",
                    "overview": "TMDB 单集简介",
                    "air_date": "2008-01-20",
                }
            ],
        }
        models.save_tv_episode(
            self.database_path,
            series,
            season,
            1,
            "/TV/Breaking Bad/S01E01.mkv",
            series_images=[],
        )
        saved = models.get_media_detail(self.database_path, "tv", 999100)
        episode = saved["seasons"][0]["episodes"][0]
        self.assertEqual(episode["imdb_id"], "tt0959621")
        self.assertEqual(episode["air_date"], "2008-01-20")
        self.assertEqual(len(saved["sources"]), 1)


class ConnectorUnitTest(unittest.TestCase):
    def test_tmdb_detail_merges_external_imdb_id(self):
        with patch.object(
            helpers,
            "_tmdb_get",
            side_effect=[
                {"id": 123, "name": "Series"},
                {"id": 123, "imdb_id": "tt0903747"},
            ],
        ) as tmdb_get:
            details = helpers.get_media_details("tv", 123)

        self.assertEqual(details["imdb_id"], "tt0903747")
        self.assertEqual(tmdb_get.call_args_list[1].args[0], "/tv/123/external_ids")

    def test_movie_detail_reuses_embedded_imdb_id(self):
        with patch.object(
            helpers,
            "_tmdb_get",
            return_value={"id": 123, "title": "Movie", "imdb_id": "tt0111161"},
        ) as tmdb_get:
            details = helpers.get_media_details("movie", 123)

        self.assertEqual(details["imdb_id"], "tt0111161")
        tmdb_get.assert_called_once_with("/movie/123", {"language": helpers.language})

    def test_alist_url_is_restricted_to_signed_local_direct_links(self):
        valid = "http://127.0.0.1:5244/d/Movies/Test.mkv?sign=abc"
        self.assertEqual(helpers.parse_alist_url(valid), "/Movies/Test.mkv")

        for invalid in (
            "https://example.com/d/Movies/Test.mkv?sign=abc",
            "http://127.0.0.1:5244/d/Movies/Test.mkv",
            "http://127.0.0.1:5244/d/../secret.mkv?sign=abc",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                helpers.parse_alist_url(invalid)

    def test_ffprobe_output_is_normalized(self):
        payload = {
            "format": {
                "duration": "12.345",
                "size": "1024",
                "bit_rate": "8000000",
                "format_name": "matroska,webm",
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "avg_frame_rate": "24000/1001",
                    "pix_fmt": "yuv420p10le",
                    "color_transfer": "smpte2084",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "channels": 6,
                    "channel_layout": "5.1",
                },
            ],
        }
        completed = Mock(returncode=0, stdout=json.dumps(payload))
        with patch.object(helpers.subprocess, "run", return_value=completed):
            result = helpers.probe_video("https://media.test/movie.mkv")

        self.assertEqual(result["media_duration_ms"], 12_345)
        self.assertEqual(result["video_width"], 3840)
        self.assertEqual(result["video_frame_rate"], 23.976)
        self.assertEqual(result["video_dynamic_range"], "HDR10 / PQ")
        self.assertEqual(result["audio_channels"], 6)

    def test_image_download_is_capped_at_five_per_category(self):
        links = {
            "posters": [f"https://image.test/poster-{i}.jpg" for i in range(8)],
            "logos": [f"https://image.test/logo-{i}.png" for i in range(7)],
            "backdrops": [f"https://image.test/backdrop-{i}.jpg" for i in range(6)],
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(helpers, "_download_to_target") as download:
                records = helpers.download_media_images("movie", 123, links, directory)

        self.assertEqual(len(records), 15)
        self.assertEqual(download.call_count, 15)
        self.assertEqual({record["image_position"] for record in records}, {1, 2, 3, 4, 5})

    def test_obsolete_omdb_connector_is_absent(self):
        for name in (
            "fetch_omdb_ratings",
            "fetch_omdb_season_episodes",
            "fetch_media_ratings",
            "parse_omdb_ratings",
            "RatingFetchError",
        ):
            self.assertFalse(hasattr(helpers, name), name)


if __name__ == "__main__":
    unittest.main()
