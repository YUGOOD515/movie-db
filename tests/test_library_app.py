import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import unquote

import app as movie_app
import db as database_module
import helpers
import models
from playback import is_effectively_completed
from queries.collection import get_collection_items
from queries.title import query_title
from viewmodels.title import build_title_viewmodel


class LibraryIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "library.db"
        schema = (movie_app.BASE_DIR / "migrations" / "library_schema.sql").read_text(
            encoding="utf-8"
        )
        fixture = (
            movie_app.BASE_DIR / "tests" / "fixtures" / "library_fixture.sql"
        ).read_text(encoding="utf-8")
        with closing(sqlite3.connect(self.database_path)) as database:
            database.executescript(schema)
            database.executescript(fixture)

        self.previous_database = movie_app.app.config["DATABASE"]
        movie_app.app.config.update(TESTING=True, DATABASE=self.database_path)
        self.client = movie_app.app.test_client()

    def tearDown(self):
        movie_app.app.config["DATABASE"] = self.previous_database
        self.temporary_directory.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def build_title(
        self,
        title_id: str,
        *,
        select_resume: bool = False,
        **parameters,
    ):
        with movie_app.app.test_request_context("/"):
            raw = query_title(database_module.get_db(), title_id, **parameters)
            return build_title_viewmodel(raw, select_resume=select_resume)

    def test_schema_data_and_integrity(self):
        with closing(self.connect()) as database:
            tables = {
                row[0]
                for row in database.execute(
                    """
                    SELECT name
                      FROM sqlite_master
                     WHERE type = 'table'
                       AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
            self.assertEqual(
                tables,
                {
                    "title_basic",
                    "title_relation",
                    "title_artwork",
                    "media_file",
                    "playback_progress",
                    "collection",
                    "collection_item",
                },
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM title_basic").fetchone()[0],
                8,
            )
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        with closing(database_module.connect_database(self.database_path)) as database:
            with self.assertRaises(sqlite3.OperationalError):
                database.execute("DELETE FROM collection_item")

    def test_core_routes_and_legacy_redirects(self):
        for path in (
            "/",
            "/recent",
            "/unwatched",
            "/top",
            "/catalog",
            "/catalog?type=series&genre=Drama&rating=8&language=en&sort=rating",
            "/favorites",
            "/collections",
            "/collection/1",
            "/title/tt0816692",
            "/title/tt6226232",
            "/title/tt6226232?resume=1",
            "/title/tt6226232?season=3&episode=12",
            "/title/tt11555014",
            "/media",
            "/media?q=星际&type=movie",
            "/media?tab=associate",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        self.assertEqual(self.client.get("/movies/157336").status_code, 308)
        self.assertEqual(self.client.get("/series/71728").status_code, 308)

    def test_home_catalog_and_title_viewmodels_render_expected_data(self):
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("S3 E13 · 合同，规则和一点猪脑", home)
        self.assertIn("/title/tt6226232?resume=1", home)
        self.assertIn('action="/title/tt6226232/resume"', home)
        self.assertIn("电影类别", home)
        self.assertIn("剧集类别", home)
        self.assertIn("观看进度", home)
        self.assertEqual(home.count("media-rail--span-6"), 3)
        self.assertEqual(home.count("media-rail--span-3"), 1)
        self.assertEqual(home.count("media-rail--span-2"), 3)
        self.assertRegex(
            home,
            r'continue-frame[\s\S]+?</div>\s*<div class="continue-meta pt-3">',
        )

        catalog = self.client.get(
            "/catalog?type=movie&genre=Sci-Fi&rating=8&language=en&sort=rating"
        ).get_data(as_text=True)
        self.assertIn("星际穿越", catalog)
        self.assertNotIn("后室海报", catalog)
        self.assertIn("segmented-control", catalog)
        self.assertIn("genre-pills", catalog)

        default_series = self.client.get("/title/tt6226232").get_data(as_text=True)
        selected_series = self.client.get(
            "/title/tt6226232?season=3&episode=12"
        ).get_data(as_text=True)
        direct_episode = self.client.get("/title/tt11555014").get_data(as_text=True)
        self.assertIn("S1 · E1", default_series)
        self.assertIn("S3 · E12", selected_series)
        self.assertIn("身体闪光器和商场安全装备", selected_series)
        self.assertIn("S3 · E12", direct_episode)
        self.assertIn("detail-hero-grid", selected_series)
        self.assertIn("episode-rail-track", selected_series)
        information_position = selected_series.index('id="information-heading"')
        file_position = selected_series.index('id="file-information-heading"')
        links_position = selected_series.index('aria-label="外部链接"')
        self.assertLess(information_position, file_position)
        self.assertLess(file_position, links_position)
        self.assertNotIn('class="col-12 col-lg-7"', selected_series)
        self.assertNotIn('class="col-12 col-lg-5"', selected_series)

        # 懒渲染：服务端只输出精选单集的面板，其余单集走 JSON 数据岛
        self.assertEqual(selected_series.count('data-episode-hero="'), 1)
        self.assertEqual(selected_series.count('data-episode-hero-control="'), 1)
        self.assertEqual(selected_series.count('data-episode-facts="'), 1)
        self.assertIn('data-episode-data', selected_series)
        self.assertGreater(
            selected_series.count('data-episode-target="'),
            selected_series.count('data-episode-hero="'),
        )

        manager = self.client.get("/media?q=星际&type=movie").get_data(
            as_text=True
        )
        self.assertIn("overview-artwork-row", manager)
        self.assertIn("tt0816692", manager)

        favorites = self.client.get("/favorites").get_data(as_text=True)
        self.assertIn("收藏夹筛选", favorites)
        self.assertIn("genre-pills", favorites)

        top = self.client.get("/top").get_data(as_text=True)
        ranked_cards = re.findall(
            r'<article class="ranked-feature">([\s\S]*?)</article>',
            top,
        )
        self.assertEqual(len(ranked_cards), 2)
        for card in ranked_cards:
            self.assertIn("ranked-feature-logo", card)
            self.assertIn("ranked-feature-content", card)
            self.assertIn("ranked-feature-meta", card)
            self.assertNotIn('class="fw-semibold text-truncate"', card)

        association = self.client.get("/media?tab=associate").get_data(
            as_text=True
        )
        self.assertIn("<fieldset disabled>", association)

    def test_missing_resources_return_404(self):
        self.assertEqual(self.client.get("/title/tt0000000").status_code, 404)
        self.assertEqual(self.client.get("/collection/9999").status_code, 404)
        self.assertEqual(self.client.post("/play/9999999").status_code, 404)

    def test_collection_playback_and_artwork_mutations(self):
        self.assertTrue(
            models.toggle_library_collection_item(
                self.database_path, 1, "tt0816692"
            )
        )
        self.assertFalse(
            models.toggle_library_collection_item(
                self.database_path, 1, "tt0816692"
            )
        )
        with self.assertRaises(ValueError):
            models.toggle_library_collection_item(
                self.database_path, 2, "tt11555014"
            )
        self.assertTrue(
            models.toggle_library_collection_item(
                self.database_path, 2, "tt6226232"
            )
        )

        with closing(self.connect()) as database:
            before = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 2
                """
            ).fetchone()[0]
        models.set_library_primary_artwork(
            self.database_path, "tt0816692", "poster", 2
        )
        with closing(self.connect()) as database:
            primary = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 1
                """
            ).fetchone()[0]
            self.assertEqual(primary, before)
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        response = self.client.post(
            "/manager/tt0816692/artwork",
            data={
                "poster_position": "2",
                "backdrop_position": "2",
                "logo_position": "2",
                "return_to": "/media",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/media"))
        with closing(self.connect()) as database:
            primary_types = database.execute(
                """
                SELECT COUNT(DISTINCT artwork_type)
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type IN ('poster', 'landscape_poster', 'logo')
                   AND position = 1
                """
            ).fetchone()[0]
            self.assertEqual(primary_types, 3)
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        playback_id = models.save_library_playback_progress(
            self.database_path, 3, 1_000
        )
        models.save_library_playback_progress(
            self.database_path, 3, 2_000, playback_id=playback_id
        )
        with closing(self.connect()) as database:
            row = database.execute(
                "SELECT position_ms FROM playback_progress WHERE playback_id = ?",
                (playback_id,),
            ).fetchone()
            self.assertEqual(row["position_ms"], 2_000)

    def test_play_route_uses_one_history_row_per_session(self):
        with closing(self.connect()) as database:
            before = database.execute(
                "SELECT COUNT(*) FROM playback_progress WHERE file_id = 3"
            ).fetchone()[0]

        with patch.object(helpers, "get_video_link", return_value="https://media.test/a.mkv"), patch.object(
            helpers, "play_video"
        ) as play_video:
            response = self.client.post("/play/3")
            self.assertEqual(response.status_code, 204)
            callback = play_video.call_args.kwargs["progress_callback"]
            callback(1_000, 100_000)
            callback(2_000, 100_000)

        with closing(self.connect()) as database:
            rows = database.execute(
                """
                SELECT position_ms
                  FROM playback_progress
                 WHERE file_id = 3
                 ORDER BY playback_id
                """
            ).fetchall()
            self.assertEqual(len(rows), before + 1)
            self.assertEqual(rows[-1]["position_ms"], 2_000)

    def test_series_resume_thresholds_and_collection_identity(self):
        page = self.build_title("tt6226232")
        self.assertEqual(page["selected_episode"]["episode_id"], "tt6719334")
        self.assertEqual(page["resume_episode"]["episode_id"], "tt11591954")
        self.assertTrue(page["resume_episode"]["play_label"].startswith("继续播放"))
        self.assertEqual(page["favorite"]["title_id"], "tt6226232")
        self.assertTrue(page["favorite"]["action_url"].endswith("/tt6226232/toggle"))

        with closing(self.connect()) as database, database:
            database.execute(
                "UPDATE media_file SET duration_ms = 600000 WHERE file_id = 14"
            )
            database.execute(
                "UPDATE playback_progress SET position_ms = 480000 WHERE file_id = 14"
            )
        page = self.build_title("tt6226232")
        self.assertEqual(page["resume_episode"]["episode_id"], "tt11591954")

        with closing(self.connect()) as database, database:
            database.execute(
                "UPDATE playback_progress SET position_ms = 480001 WHERE file_id = 14"
            )
        page = self.build_title("tt6226232")
        self.assertEqual(page["resume_episode"]["episode_id"], "tt11599999")
        self.assertTrue(page["resume_episode"]["play_label"].startswith("播放"))
        resume_page = self.build_title("tt6226232", select_resume=True)
        self.assertEqual(
            resume_page["selected_episode"]["episode_id"],
            "tt11599999",
        )
        self.assertEqual(
            resume_page["playback_episode"]["episode_id"],
            "tt11599999",
        )
        rendered = self.client.get("/title/tt6226232?resume=1").get_data(
            as_text=True
        )
        self.assertIn('data-episode-hero="tt11599999"', rendered)
        response = self.client.post("/title/tt6226232/resume")
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["Location"].endswith("/play/15"))

        with closing(self.connect()) as database, database:
            database.execute(
                "UPDATE playback_progress SET position_ms = 540000 WHERE file_id = 14"
            )
        ninety_percent = self.build_title("tt6226232", select_resume=True)
        self.assertEqual(
            ninety_percent["selected_episode"]["episode_id"],
            "tt11599999",
        )

        self.assertFalse(is_effectively_completed(480000, 600000))
        self.assertTrue(is_effectively_completed(480001, 600000))
        self.assertTrue(is_effectively_completed(540000, 600000))

        models.toggle_library_collection_item(
            self.database_path, 2, "tt6226232"
        )
        with closing(self.connect()) as database:
            items = get_collection_items(database, 2)
        self.assertEqual([item["title_id"] for item in items], ["tt6226232"])

    def test_completed_movie_restarts_and_uses_non_continue_label(self):
        with closing(self.connect()) as database, database:
            database.execute(
                """
                INSERT INTO playback_progress (file_id, position_ms, watched_at)
                VALUES (3, 9126000, '2026-08-30 10:00:00')
                """
            )
        page = self.build_title("tt0816692")
        self.assertFalse(page["play_label"].startswith("继续播放"))

        with patch.object(
            helpers, "get_video_link", return_value="https://media.test/movie.mkv"
        ), patch.object(helpers, "play_video") as play_video:
            self.assertEqual(self.client.post("/play/3").status_code, 204)
        self.assertEqual(play_video.call_args.kwargs["start_position_ms"], 0)

    def test_artwork_batch_rolls_back_as_one_transaction(self):
        with closing(self.connect()) as database:
            before = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 1
                """
            ).fetchone()[0]
        response = self.client.post(
            "/manager/tt0816692/artwork",
            data={
                "poster_position": "2",
                "backdrop_position": "999",
                "return_to": "/media",
            },
        )
        self.assertEqual(response.status_code, 404)
        with closing(self.connect()) as database:
            after = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 1
                """
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_manager_filtering_is_server_owned(self):
        html = self.client.get("/media?q=星际&type=movie").get_data(as_text=True)
        self.assertIn("overviewForm.requestSubmit()", html)
        self.assertNotIn("function applyFilter", html)
        self.assertNotIn("request.full_path", html)

    def test_rendered_local_static_references_exist(self):
        references = set()
        for path in (
            "/",
            "/top",
            "/title/tt0816692",
            "/title/tt6226232?season=3&episode=12",
            "/media?q=星际&type=movie",
        ):
            html = self.client.get(path).get_data(as_text=True)
            references.update(
                unquote(match)
                for match in re.findall(r'(?:src|href)="/static/([^"?#]+)', html)
            )
        missing = [
            reference
            for reference in references
            if not (movie_app.BASE_DIR / "static" / reference).is_file()
        ]
        self.assertGreater(len(references), 4)
        self.assertEqual(missing, [])


class ConnectorUnitTest(unittest.TestCase):
    def test_connector_credentials_come_from_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TMDB_TOKEN"):
                helpers._tmdb_headers()
            with self.assertRaisesRegex(RuntimeError, "ALIST_USERNAME"):
                helpers.get_video_link("/Movies/Test.mkv")

        with patch.dict(os.environ, {"TMDB_TOKEN": "test-token"}, clear=True):
            self.assertEqual(
                helpers._tmdb_headers()["Authorization"],
                "Bearer test-token",
            )

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
                records = helpers.download_media_images("tt1234567", links, directory)

        self.assertEqual(len(records), 15)
        self.assertEqual(download.call_count, 15)
        self.assertEqual(
            {record["image_position"] for record in records},
            {1, 2, 3, 4, 5},
        )

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
