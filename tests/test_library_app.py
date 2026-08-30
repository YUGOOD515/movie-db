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
from connectors import alist, media_probe, player
from models import database as database_module
from models import library as models
from queries.collection import get_collection_items
from queries.title import query_title
from services import media_import as media_import_service
from services.playback import is_effectively_completed
from scripts import fetch_tmdb_metadata as tmdb_connector
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

        self.imdb_path = Path(self.temporary_directory.name) / "imdb.db"
        with closing(sqlite3.connect(self.imdb_path)) as imdb:
            imdb.executescript(
                """
                CREATE TABLE title_basics (
                    tconst TEXT PRIMARY KEY,
                    titleType TEXT,
                    primaryTitle TEXT,
                    originalTitle TEXT,
                    isAdult INTEGER,
                    startYear INTEGER,
                    endYear INTEGER,
                    runtimeMinutes INTEGER,
                    genres TEXT
                );
                CREATE TABLE title_akas (
                    titleId TEXT,
                    ordering INTEGER,
                    title TEXT,
                    region TEXT,
                    language TEXT,
                    types TEXT,
                    attributes TEXT,
                    isOriginalTitle INTEGER,
                    PRIMARY KEY (titleId, ordering)
                );
                CREATE TABLE title_ratings (
                    tconst TEXT PRIMARY KEY,
                    averageRating REAL,
                    numVotes INTEGER
                );
                CREATE TABLE title_episode (
                    tconst TEXT PRIMARY KEY,
                    parentTconst TEXT,
                    seasonNumber INTEGER,
                    episodeNumber INTEGER
                );
                INSERT INTO title_basics VALUES
                    ('tt9000001', 'movie', 'Import Movie', 'Import Movie', 0, 2026, NULL, 120, 'Drama'),
                    ('tt9000002', 'tvSeries', 'Import Series', 'Import Series', 0, 2026, NULL, 50, 'Drama'),
                    ('tt9000003', 'tvEpisode', 'First Episode', 'First Episode', 0, 2026, NULL, 50, 'Drama'),
                    ('tt9000004', 'tvEpisode', 'Second Episode', 'Second Episode', 0, 2026, NULL, 51, 'Drama');
                INSERT INTO title_akas VALUES
                    ('tt9000001', 1, '导入电影', 'CN', 'zh', NULL, NULL, 0),
                    ('tt9000002', 1, '导入剧集', 'CN', 'zh', NULL, NULL, 0),
                    ('tt9000003', 1, '第一集', 'CN', 'zh', NULL, NULL, 0),
                    ('tt9000004', 1, '第二集', 'CN', 'zh', NULL, NULL, 0);
                INSERT INTO title_ratings VALUES
                    ('tt9000001', 8.1, 1000),
                    ('tt9000002', 8.2, 2000),
                    ('tt9000003', 8.3, 300),
                    ('tt9000004', 8.4, 400);
                INSERT INTO title_episode VALUES
                    ('tt9000003', 'tt9000002', 1, 1),
                    ('tt9000004', 'tt9000002', 1, 2);
                """
            )

        self.previous_database = movie_app.app.config["DATABASE"]
        self.previous_imdb_database = movie_app.app.config["IMDB_DATABASE"]
        self.previous_image_root = movie_app.app.config["IMAGE_ROOT"]
        movie_app.app.config.update(
            TESTING=True,
            DATABASE=self.database_path,
            IMDB_DATABASE=self.imdb_path,
            IMAGE_ROOT=Path(self.temporary_directory.name) / "images",
        )
        self.client = movie_app.app.test_client()

    def tearDown(self):
        movie_app.app.config["DATABASE"] = self.previous_database
        movie_app.app.config["IMDB_DATABASE"] = self.previous_imdb_database
        movie_app.app.config["IMAGE_ROOT"] = self.previous_image_root
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
        # 默认进入（未指定单集）按历史记录规则定位到续播单集 S3·E13
        self.assertIn("S3 · E13", default_series)
        self.assertIn('data-episode-hero="tt11591954"', default_series)
        self.assertIn("继续播放", default_series)
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

        # 全剧/本集信息切换：按钮 + 全剧面板（默认隐藏）
        self.assertIn('data-series-toggle', selected_series)
        self.assertIn('data-series-hero-panel', selected_series)
        self.assertIn('series-hero-panel hero-pane-hidden', selected_series)
        self.assertIn("全剧 · 共", selected_series)
        self.assertNotIn("data-series-toggle", self.client.get("/title/tt0816692").get_data(as_text=True))
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
        self.assertIn('action="/manager/import"', association)
        self.assertNotIn("<fieldset disabled>", association)

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

        with patch.object(alist, "get_video_link", return_value="https://media.test/a.mkv"), patch.object(
            player, "play_video"
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
        self.assertEqual(page["selected_episode"]["episode_id"], "tt11591954")
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
            alist, "get_video_link", return_value="https://media.test/movie.mkv"
        ), patch.object(player, "play_video") as play_video:
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

    def test_import_writer_rejects_cross_title_alist_remap_atomically(self):
        with closing(self.connect()) as database:
            existing_path = database.execute(
                "SELECT alist_path FROM media_file ORDER BY file_id LIMIT 1"
            ).fetchone()[0]

        title = {
            "title_id": "tt9000001",
            "tmdb_id": 9001,
            "title_type": "movie",
            "primary_title": "Import Movie",
            "original_title": "Import Movie",
            "original_language": "en",
            "display_title": "导入电影",
            "is_adult": 0,
            "start_year": 2026,
            "end_year": None,
            "runtime_minutes": 120,
            "genres": "Drama",
            "overview": "电影简介",
            "tagline": None,
            "average_rating": 8.1,
            "num_votes": 1000,
        }
        with self.assertRaisesRegex(ValueError, "已关联其他影视条目"):
            models.import_media_bundle(
                self.database_path,
                titles=[title],
                relations=[],
                artwork=[],
                target_title_id=title["title_id"],
                alist_path=existing_path,
                probe={},
            )

        with closing(self.connect()) as database:
            inserted = database.execute(
                "SELECT 1 FROM title_basic WHERE title_id = 'tt9000001'"
            ).fetchone()
        self.assertIsNone(inserted)

    def test_manager_filtering_is_server_owned(self):
        html = self.client.get("/media?q=星际&type=movie").get_data(as_text=True)
        self.assertIn("overviewForm.requestSubmit()", html)
        self.assertNotIn("function applyFilter", html)
        self.assertNotIn("request.full_path", html)

    def test_title_id_import_accepts_movie_series_and_episode(self):
        movie_metadata = {
            "title_basic": {
                "tmdb_id": 9001,
                "overview": "电影简介",
                "tagline": "电影标语",
                "original_language": "en",
            },
            "title_artwork": [],
            "episodes": [],
        }
        series_metadata = {
            "title_basic": {
                "tmdb_id": 9002,
                "overview": "剧集简介",
                "tagline": "剧集标语",
                "original_language": "en",
            },
            "title_artwork": [],
            "episodes": [
                {
                    "season_number": 1,
                    "episode_number": 1,
                    "title_basic": {
                        "tmdb_id": 9003,
                        "overview": "第一集简介",
                        "tagline": None,
                        "original_language": "en",
                    },
                    "title_artwork": [],
                },
                {
                    "season_number": 1,
                    "episode_number": 2,
                    "title_basic": {
                        "tmdb_id": 9004,
                        "overview": "第二集简介",
                        "tagline": None,
                        "original_language": "en",
                    },
                    "title_artwork": [],
                },
            ],
        }

        def metadata(title_id):
            return movie_metadata if title_id == "tt9000001" else series_metadata

        probe = {
            "duration_ms": 7_200_000,
            "file_size_bytes": 1024,
            "bit_rate": 8_000_000,
            "container_format": "matroska,webm",
            "video_codec": "hevc",
            "video_width": 3840,
            "video_height": 2160,
            "video_frame_rate": 23.976,
            "video_pixel_format": "yuv420p10le",
            "video_dynamic_range": "HDR10 / PQ",
            "audio_codec": "eac3",
            "audio_channels": 6,
            "audio_channel_layout": "5.1",
        }
        with patch.object(
            media_import_service.alist, "validate_alist_video_url"
        ), patch.object(
            media_import_service.alist,
            "get_video_link",
            return_value="https://media.test/file.mkv",
        ), patch.object(
            media_import_service.media_probe, "probe_video", return_value=probe
        ), patch.object(
            media_import_service.tmdb,
            "build_media_metadata",
            side_effect=metadata,
        ):
            movie = self.client.post(
                "/manager/import",
                data={
                    "title_id": "tt9000001",
                    "alist_url": "http://127.0.0.1:5244/d/Movies/Import.mkv?sign=one",
                },
            )
            series_episode = self.client.post(
                "/manager/import",
                data={
                    "title_id": "tt9000002",
                    "season_number": "1",
                    "episode_number": "1",
                    "alist_url": "http://127.0.0.1:5244/d/Series/S01E01.mkv?sign=two",
                },
            )
            direct_episode = self.client.post(
                "/manager/import",
                data={
                    "title_id": "tt9000004",
                    "alist_url": "http://127.0.0.1:5244/d/Series/S01E02.mkv?sign=three",
                },
            )

        self.assertEqual(movie.status_code, 302)
        self.assertEqual(series_episode.status_code, 302)
        self.assertEqual(direct_episode.status_code, 302)
        with closing(self.connect()) as database:
            imported_titles = database.execute(
                """
                SELECT title_id, title_type, display_title, average_rating
                  FROM title_basic
                 WHERE title_id LIKE 'tt900%'
                 ORDER BY title_id
                """
            ).fetchall()
            imported_files = database.execute(
                """
                SELECT title_id, alist_path, duration_ms
                  FROM media_file
                 WHERE title_id LIKE 'tt900%'
                 ORDER BY alist_path
                """
            ).fetchall()
            relations = database.execute(
                """
                SELECT episode_id, series_id, season_number, episode_number
                  FROM title_relation
                 WHERE series_id = 'tt9000002'
                 ORDER BY episode_number
                """
            ).fetchall()

        self.assertEqual(len(imported_titles), 4)
        self.assertEqual(imported_titles[0]["display_title"], "导入电影")
        self.assertEqual(imported_titles[0]["average_rating"], 8.1)
        self.assertEqual(
            [row["title_id"] for row in imported_files],
            ["tt9000001", "tt9000003", "tt9000004"],
        )
        self.assertTrue(all(row["duration_ms"] == 7_200_000 for row in imported_files))
        self.assertEqual(len(relations), 2)

    def test_series_title_id_requires_position_before_external_calls(self):
        with patch.object(
            media_import_service.tmdb, "build_media_metadata"
        ) as fetch_metadata:
            response = self.client.post(
                "/manager/import",
                data={
                    "title_id": "tt9000002",
                    "alist_url": "http://127.0.0.1:5244/d/Series/Test.mkv?sign=abc",
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("必须填写有效的季号和集号", response.get_data(as_text=True))
        fetch_metadata.assert_not_called()

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
        with tempfile.TemporaryDirectory() as directory, patch.object(
            tmdb_connector, "PROJECT_ROOT", Path(directory)
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TMDB_TOKEN"):
                tmdb_connector._token()
            with self.assertRaisesRegex(RuntimeError, "ALIST_USERNAME"):
                alist.get_video_link("/Movies/Test.mkv")

        with patch.dict(os.environ, {"TMDB_TOKEN": "test-token"}, clear=True):
            self.assertEqual(tmdb_connector._token(), "test-token")

    def test_alist_url_is_restricted_to_signed_local_direct_links(self):
        valid = "http://127.0.0.1:5244/d/Movies/Test.mkv?sign=abc"
        self.assertEqual(alist.parse_alist_url(valid), "/Movies/Test.mkv")

        for invalid in (
            "https://example.com/d/Movies/Test.mkv?sign=abc",
            "http://127.0.0.1:5244/d/Movies/Test.mkv",
            "http://127.0.0.1:5244/d/../secret.mkv?sign=abc",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                alist.parse_alist_url(invalid)

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
        with patch.object(media_probe.subprocess, "run", return_value=completed):
            result = media_probe.probe_video("https://media.test/movie.mkv")

        self.assertEqual(result["duration_ms"], 12_345)
        self.assertEqual(result["video_width"], 3840)
        self.assertEqual(result["video_frame_rate"], 23.976)
        self.assertEqual(result["video_dynamic_range"], "HDR10 / PQ")
        self.assertEqual(result["audio_channels"], 6)

    def test_image_download_is_capped_at_five_per_category(self):
        images = {
            "posters": [
                {"file_path": f"/poster-{i}.jpg", "iso_639_1": "zh"}
                for i in range(8)
            ],
            "logos": [
                {"file_path": f"/logo-{i}.png", "iso_639_1": "zh"}
                for i in range(7)
            ],
            "backdrops": [
                {"file_path": f"/backdrop-{i}.jpg", "iso_639_1": None}
                for i in range(6)
            ],
        }
        records = tmdb_connector.build_title_artwork("tt1234567", images)

        self.assertEqual(len(records), 15)
        self.assertEqual(
            {record["position"] for record in records},
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
            self.assertFalse(hasattr(tmdb_connector, name), name)


if __name__ == "__main__":
    unittest.main()
