import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import requests

import app as movie_app


class ImportWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        schema = (movie_app.BASE_DIR / "migrations" / "schema.sql").read_text(
            encoding="utf-8"
        )

        with closing(sqlite3.connect(self.database_path)) as database:
            with database:
                database.executescript(schema)

        movie_app.app.config.update(
            DATABASE=self.database_path,
            IMAGE_ROOT=Path(self.temporary_directory.name) / "static" / "images",
            TESTING=True,
        )
        self.client = movie_app.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_home_contains_library_sections_and_media_route_contains_import_page(self):
        home_response = self.client.get("/")
        media_response = self.client.get("/media")
        recent_added_response = self.client.get("/recent-added")
        unwatched_response = self.client.get("/unwatched")
        favorites_response = self.client.get("/favorites")
        genre_response = self.client.get("/genres")
        top_rated_response = self.client.get("/top-rated")

        self.assertEqual(home_response.status_code, 200)
        self.assertNotIn('action="/media"'.encode(), home_response.data)
        self.assertNotIn(b"library-heading", home_response.data)
        self.assertNotIn(b"continue-heading", home_response.data)
        for section_name in (
            "最近观看",
            "收藏夹",
            "最近添加",
            "电影类别",
            "剧集类别",
            "未观看",
            "最受好评的电影",
        ):
            self.assertIn(section_name.encode(), home_response.data)
        self.assertIn("还没有观看记录".encode(), home_response.data)
        self.assertIn("media-rail--span-3".encode(), home_response.data)
        self.assertIn(b"site-menu-toggle", home_response.data)
        self.assertIn(b"site-sidebar offcanvas offcanvas-start", home_response.data)
        self.assertNotIn(b"offcanvas-lg", home_response.data)
        self.assertIn(b'href="/recent-added"', home_response.data)
        self.assertIn(b'href="/unwatched"', home_response.data)
        self.assertIn(b'href="/genres"', home_response.data)
        self.assertIn(b'href="/top-rated"', home_response.data)
        for href in (
            "/recent-added",
            "/favorites",
            "/genres",
            "/unwatched",
            "/top-rated",
        ):
            self.assertIn(f'href="{href}"'.encode(), home_response.data)
        self.assertEqual(media_response.status_code, 200)
        self.assertIn("媒体资源管理".encode(), media_response.data)
        self.assertIn('action="/media"'.encode(), media_response.data)
        self.assertEqual(recent_added_response.status_code, 200)
        self.assertIn("最近添加".encode(), recent_added_response.data)
        self.assertIn("媒体库暂时为空".encode(), recent_added_response.data)
        self.assertEqual(unwatched_response.status_code, 200)
        self.assertIn("未观看".encode(), unwatched_response.data)
        self.assertIn("目前没有未观看的媒体".encode(), unwatched_response.data)
        self.assertEqual(favorites_response.status_code, 200)
        self.assertIn("当前收藏夹还没有媒体".encode(), favorites_response.data)
        self.assertEqual(genre_response.status_code, 200)
        self.assertIn("制作国家".encode(), genre_response.data)
        self.assertIn("筛选结果".encode(), genre_response.data)
        self.assertEqual(top_rated_response.status_code, 200)
        self.assertIn("最受好评的电影".encode(), top_rated_response.data)

    def test_missing_media_detail_returns_not_found(self):
        self.assertEqual(self.client.get("/movies/999999").status_code, 404)
        self.assertEqual(self.client.get("/series/999999").status_code, 404)
        self.assertEqual(self.client.get("/play/movie/999999").status_code, 404)
        self.assertEqual(self.client.get("/play/invalid/1").status_code, 404)
        self.assertEqual(self.client.get("/favorites?folder_id=999999").status_code, 404)
        self.assertEqual(
            self.client.post("/favorites/invalid/1/toggle").status_code,
            404,
        )

    def test_rejects_non_local_alist_url(self):
        response = self.client.post(
            "/media",
            data={
                "movie_name": "测试电影",
                "alist_url": "https://example.com/movie.mkv",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("AList 链接必须来自".encode(), response.data)

    def test_requires_signed_direct_link(self):
        with self.assertRaisesRegex(ValueError, "/d/"):
            movie_app.helpers.parse_alist_url(
                "http://127.0.0.1:5244/Quark/movie.mkv"
            )

        with self.assertRaisesRegex(ValueError, "sign"):
            movie_app.helpers.parse_alist_url(
                "http://127.0.0.1:5244/d/Quark/movie.mkv"
            )

    @patch("helpers.requests.get")
    def test_reads_status_and_seeks_through_mpc_web_api(self, mocked_get):
        mocked_get.return_value.json.return_value = {
            "file": "movie.mkv",
            "path": "https://alist.example/raw/movie.mkv?token=test",
            "state": 2,
            "position": 3_723_456,
            "duration": 7_200_000,
        }

        status = movie_app.helpers.get_mpc_status()

        self.assertEqual(status["position_ms"], 3_723_456)
        self.assertEqual(status["duration_ms"], 7_200_000)
        self.assertFalse(
            movie_app.helpers._status_matches_video(
                {"file": "movie.mkv", "path": "https://other/movie.mkv"},
                "https://alist.example/raw/movie.mkv",
            )
        )
        mocked_get.assert_called_once_with(
            "http://127.0.0.1:13579/status.json",
            timeout=2,
        )

        mocked_get.reset_mock()
        movie_app.helpers.seek_mpc(3_723_456)
        mocked_get.assert_called_once_with(
            "http://127.0.0.1:13579/command.html",
            params={
                "wm_command": "-1",
                "position": "01:02:03.456",
            },
            timeout=2,
        )

    @patch("helpers.time.sleep")
    @patch("helpers.seek_mpc")
    @patch("helpers.get_mpc_status")
    def test_mpc_monitor_seeks_once_and_saves_changed_progress(
        self,
        mocked_status,
        mocked_seek,
        mocked_sleep,
    ):
        mocked_status.side_effect = [
            {
                "file": "movie.mkv",
                "path": "https://alist.example/raw/movie.mkv?token=loading",
                "state": 0,
                "position_ms": 0,
                "duration_ms": 0,
            },
            {
                "file": "movie.mkv",
                "path": "https://alist.example/raw/movie.mkv?token=one",
                "state": 2,
                "position_ms": 0,
                "duration_ms": 7_200_000,
            },
            {
                "file": "movie.mkv",
                "path": "https://alist.example/raw/movie.mkv?token=two",
                "state": 2,
                "position_ms": 601_000,
                "duration_ms": 7_200_000,
            },
            {
                "file": "movie.mkv",
                "path": "https://alist.example/raw/movie.mkv?token=three",
                "state": 0,
                "position_ms": 602_000,
                "duration_ms": 7_200_000,
            },
        ]
        saved_progress = []
        generation = movie_app.helpers._next_playback_generation()

        movie_app.helpers._monitor_mpc_playback(
            generation,
            "https://alist.example/raw/movie.mkv?token=start",
            600_000,
            lambda position, duration: saved_progress.append((position, duration)),
        )

        mocked_seek.assert_called_once_with(600_000)
        self.assertEqual(
            saved_progress,
            [(601_000, 7_200_000), (602_000, 7_200_000)],
        )

    @patch("helpers._tmdb_get")
    def test_find_media_uses_shared_movie_and_tv_flow(self, mocked_tmdb_get):
        mocked_tmdb_get.side_effect = [
            {"results": [{"id": 10}]},
            {"id": 10, "title": "测试电影"},
            {"results": [{"id": 20}]},
            {"id": 20, "name": "测试剧集"},
        ]

        movie = movie_app.helpers.find_media("movie", "测试电影")
        series = movie_app.helpers.find_media("tv", "测试剧集")

        self.assertEqual(movie["id"], 10)
        self.assertEqual(series["id"], 20)
        self.assertEqual(
            [call.args[0] for call in mocked_tmdb_get.call_args_list],
            ["/search/movie", "/movie/10", "/search/tv", "/tv/20"],
        )

    @patch("helpers.requests.get")
    def test_downloads_only_first_five_images_per_category(self, mocked_get):
        class ImageResponse:
            headers = {"Content-Type": "image/jpeg"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                return [b"test-image"]

        mocked_get.return_value = ImageResponse()
        image_links = {
            "posters": [f"https://image.tmdb.org/{number}.jpg" for number in range(6)],
            "logos": [],
            "backdrops": [],
        }

        records = movie_app.helpers.download_media_images(
            "movie",
            157336,
            image_links,
            movie_app.app.config["IMAGE_ROOT"],
        )

        self.assertEqual(len(records), 5)
        self.assertEqual(mocked_get.call_count, 5)
        for record in records:
            image_path = Path(self.temporary_directory.name) / "static" / record["local_path"]
            self.assertEqual(image_path.read_bytes(), b"test-image")

    @patch("helpers.requests.get")
    def test_downloads_tv_series_images_and_selected_episode_still(self, mocked_get):
        class ImageResponse:
            headers = {"Content-Type": "image/jpeg"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                return [b"tv-image"]

        mocked_get.return_value = ImageResponse()
        series_images = movie_app.helpers.download_media_images(
            "tv",
            1396,
            {
                "posters": ["https://image.tmdb.org/poster.jpg"],
                "logos": ["https://image.tmdb.org/logo.png"],
                "backdrops": ["https://image.tmdb.org/backdrop.jpg"],
            },
            movie_app.app.config["IMAGE_ROOT"],
        )
        episode_still = movie_app.helpers.download_tv_episode_still(
            1396,
            1,
            {"id": 62085, "still_path": "/still.jpg"},
            movie_app.app.config["IMAGE_ROOT"],
        )

        self.assertEqual(len(series_images), 3)
        self.assertEqual(mocked_get.call_count, 4)
        self.assertEqual(
            episode_still["local_path"],
            "images/tv/1396/season-1/episodes/62085.jpg",
        )
        for record in series_images + [episode_still]:
            image_path = (
                Path(self.temporary_directory.name)
                / "static"
                / record["local_path"]
            )
            self.assertEqual(image_path.read_bytes(), b"tv-image")

    @patch("helpers.requests.get")
    def test_retries_image_downloads_and_skips_one_failed_image(self, mocked_get):
        class ImageResponse:
            headers = {"Content-Type": "image/jpeg"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                return [b"test-image"]

        connection_error = requests.ConnectionError("temporary failure")
        mocked_get.side_effect = [
            connection_error,
            connection_error,
            ImageResponse(),
            connection_error,
            connection_error,
            connection_error,
        ]
        image_links = {
            "posters": [
                "https://image.tmdb.org/retry.jpg",
                "https://image.tmdb.org/skip.jpg",
            ],
            "logos": [],
            "backdrops": [],
        }

        records = movie_app.helpers.download_media_images(
            "movie",
            1327819,
            image_links,
            movie_app.app.config["IMAGE_ROOT"],
        )

        self.assertEqual(mocked_get.call_count, 6)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["image_position"], 1)
        self.assertFalse(
            (
                Path(self.temporary_directory.name)
                / "static"
                / "images"
                / "1327819"
                / "posters"
                / "2.jpg"
            ).exists()
        )

    @patch("helpers.download_media_images")
    @patch("helpers.validate_alist_video_url")
    @patch("helpers.search_media_images")
    @patch("helpers.find_media")
    def test_imports_and_lists_movie_mapping(
        self,
        mocked_find_media,
        mocked_search_media_images,
        mocked_validate_alist_video_url,
        mocked_download_media_images,
    ):
        mocked_find_media.return_value = {
            "adult": False,
            "backdrop_path": "/ignored.jpg",
            "belongs_to_collection": {
                "id": 100,
                "name": "测试系列",
                "poster_path": "/collection-poster.jpg",
                "backdrop_path": "/collection-backdrop.jpg",
            },
            "budget": 100,
            "genres": [{"id": 18, "name": "剧情"}],
            "homepage": "",
            "id": 157336,
            "imdb_id": "tt0000001",
            "origin_country": ["US"],
            "original_language": "en",
            "original_title": "Test Movie",
            "overview": "测试简介",
            "popularity": 10.5,
            "poster_path": "/ignored-poster.jpg",
            "production_companies": [
                {
                    "id": 200,
                    "logo_path": "/company.png",
                    "name": "测试公司",
                    "origin_country": "US",
                }
            ],
            "production_countries": [
                {"iso_3166_1": "US", "name": "美国"}
            ],
            "release_date": "2026-08-27",
            "revenue": 200,
            "runtime": 120,
            "softcore": False,
            "spoken_languages": [
                {"english_name": "English", "iso_639_1": "en", "name": "英语"}
            ],
            "status": "Released",
            "tagline": "",
            "title": "测试电影",
            "video": False,
            "vote_average": 8.0,
            "vote_count": 10,
        }
        mocked_search_media_images.return_value = {
            "posters": ["https://image.tmdb.org/poster.jpg"],
            "logos": [],
            "backdrops": [],
        }
        mocked_download_media_images.return_value = [
            {
                "image_type": "posters",
                "image_position": 1,
                "image_url": "https://image.tmdb.org/poster.jpg",
                "local_path": "images/157336/posters/1.jpg",
            }
        ]

        response = self.client.post(
            "/media",
            data={
                "movie_name": "测试电影",
                "alist_url": (
                    "http://127.0.0.1:5244/d/Quark/"
                    "%E6%B5%8B%E8%AF%95%E7%94%B5%E5%BD%B1.mkv?sign=test:0"
                ),
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("已关联".encode(), response.data)
        self.assertIn(
            "测试电影".encode(),
            response.data,
        )
        self.assertNotIn(b'href="http://127.0.0.1:5244', response.data)

        repeated_response = self.client.post(
            "/media",
            data={
                "movie_name": "测试电影",
                "alist_url": (
                    "http://127.0.0.1:5244/d/Quark/"
                    "%E6%B5%8B%E8%AF%95%E7%94%B5%E5%BD%B1.mkv?sign=test:0"
                ),
            },
            follow_redirects=True,
        )
        self.assertEqual(repeated_response.status_code, 200)

        with closing(sqlite3.connect(self.database_path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM movie").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM movie_source").fetchone()[0], 1)
            self.assertEqual(
                database.execute("SELECT alist_path FROM movie_source").fetchone()[0],
                "/Quark/测试电影.mkv",
            )
            self.assertEqual(database.execute("SELECT COUNT(*) FROM movie_image").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM movie_genre").fetchone()[0], 1)
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM production_company").fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM movie_spoken_language").fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute("SELECT local_path FROM movie_image").fetchone()[0],
                "images/157336/posters/1.jpg",
            )

        movie_app.models.save_watch_progress(
            self.database_path,
            "movie",
            157336,
            600_000,
            7_200_000,
        )
        homepage_data = movie_app.models.get_homepage_data(self.database_path)
        self.assertEqual(homepage_data["continue_watching"][0]["media_id"], 157336)
        self.assertEqual(homepage_data["continue_watching"][0]["progress"], 8)
        self.assertNotIn(
            157336,
            [item["media_id"] for item in homepage_data["unwatched"]],
        )
        home_response = self.client.get("/")
        recent_added_response = self.client.get("/recent-added")
        unwatched_response = self.client.get("/unwatched")
        genre_response = self.client.get("/genres")
        filtered_genre_response = self.client.get(
            "/genres?media_type=movie&genre_id=18&country_code=US"
            "&language_code=en&year=2026&min_rating=8"
        )
        empty_genre_response = self.client.get(
            "/genres?media_type=movie&min_rating=9"
        )
        top_rated_response = self.client.get("/top-rated")
        detail_response = self.client.get("/movies/157336")
        self.assertEqual(home_response.status_code, 200)
        self.assertIn("测试电影".encode(), home_response.data)
        self.assertIn("最近添加".encode(), home_response.data)
        self.assertIn("media-rail--span-6".encode(), home_response.data)
        self.assertIn("media-rail--span-2".encode(), home_response.data)
        self.assertIn("测试电影".encode(), recent_added_response.data)
        self.assertIn(b"row-cols-lg-6", recent_added_response.data)
        self.assertNotIn("测试电影".encode(), unwatched_response.data)
        self.assertIn("剧情".encode(), genre_response.data)
        self.assertIn(b"row-cols-lg-6", genre_response.data)
        self.assertIn("测试电影".encode(), filtered_genre_response.data)
        self.assertIn("没有符合当前条件的媒体".encode(), empty_genre_response.data)
        self.assertIn(b"bi bi-masks-theater", home_response.data)
        self.assertIn("测试电影".encode(), top_rated_response.data)
        self.assertIn(b"ranked-number", top_rated_response.data)
        self.assertIn(b"ranked-feature-card", top_rated_response.data)
        self.assertNotIn("第 1 名".encode(), top_rated_response.data)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("测试简介".encode(), detail_response.data)
        self.assertIn("继续播放".encode(), detail_response.data)
        self.assertIn(b'href="/play/movie/1"', detail_response.data)
        self.assertNotIn(b'href="http://127.0.0.1:5244', detail_response.data)
        self.assertIn(b"site-sidebar", detail_response.data)
        self.assertIn(b"detail-main", detail_response.data)
        self.assertIn("年龄分级".encode(), detail_response.data)
        self.assertIn("未提供".encode(), detail_response.data)
        self.assertIn("媒体信息".encode(), detail_response.data)
        self.assertIn("Test Movie".encode(), detail_response.data)
        self.assertIn("剧情".encode(), detail_response.data)
        self.assertIn("美国".encode(), detail_response.data)
        self.assertIn("英语".encode(), detail_response.data)
        self.assertIn("测试公司".encode(), detail_response.data)
        self.assertIn('action="/favorites/movie/157336/toggle"'.encode(), detail_response.data)
        self.assertIn(">收藏<".encode(), detail_response.data)

        favorite_toggle_response = self.client.post(
            "/favorites/movie/157336/toggle",
            follow_redirects=True,
        )
        movie_favorites_response = self.client.get("/favorites?folder_id=1")
        favorite_home_response = self.client.get("/")
        self.assertEqual(favorite_toggle_response.status_code, 200)
        self.assertIn("已收藏".encode(), favorite_toggle_response.data)
        self.assertIn("测试电影".encode(), movie_favorites_response.data)
        self.assertIn('href="/favorites?folder_id=1"'.encode(), favorite_home_response.data)
        self.assertIn("1 部作品".encode(), favorite_home_response.data)

        movie_app.models.toggle_media_favorite(
            self.database_path,
            "movie",
            157336,
        )
        self.assertNotIn(
            "测试电影".encode(),
            self.client.get("/favorites?folder_id=1").data,
        )
        self.assertIn("测试系列".encode(), detail_response.data)
        self.assertIn("TMDB".encode(), detail_response.data)
        self.assertIn("IMDb".encode(), detail_response.data)
        self.assertNotIn(b"detail-poster", detail_response.data)

        with (
            patch(
                "helpers.get_video_link",
                return_value="https://alist.example/raw/movie.mkv",
            ) as mocked_get_video_link,
            patch("helpers.play_video") as mocked_play_video,
        ):
            play_response = self.client.get("/play/movie/1")

        self.assertEqual(play_response.status_code, 204)
        mocked_get_video_link.assert_called_once_with("/Quark/测试电影.mkv")
        self.assertEqual(
            mocked_play_video.call_args.args,
            ("https://alist.example/raw/movie.mkv",),
        )
        self.assertEqual(
            mocked_play_video.call_args.kwargs["start_position_ms"],
            600_000,
        )
        progress_callback = mocked_play_video.call_args.kwargs["progress_callback"]
        progress_callback(1_200_000, 7_200_000)
        with closing(sqlite3.connect(self.database_path)) as database:
            self.assertEqual(
                database.execute(
                    "SELECT position_ms, duration_ms FROM movie_watch_progress"
                ).fetchone(),
                (1_200_000, 7_200_000),
            )

    @patch("helpers.download_tv_episode_still")
    @patch("helpers.download_media_images")
    @patch("helpers.search_media_images")
    @patch("helpers.validate_alist_video_url")
    @patch("helpers.search_tv_season")
    @patch("helpers.find_media")
    def test_imports_one_tv_source_and_all_selected_season_episodes(
        self,
        mocked_find_media,
        mocked_search_tv_season,
        mocked_validate_alist_video_url,
        mocked_search_media_images,
        mocked_download_media_images,
        mocked_download_tv_episode_still,
    ):
        episodes = [
            {
                "air_date": "2008-01-20",
                "crew": [{"id": 10, "name": "测试导演", "job": "Director"}],
                "episode_number": 1,
                "episode_type": "standard",
                "guest_stars": [],
                "id": 62085,
                "name": "第一集",
                "overview": "第一集简介",
                "production_code": "101",
                "runtime": 58,
                "season_number": 1,
                "show_id": 1396,
                "still_path": "/episode-1.jpg",
                "vote_average": 8.2,
                "vote_count": 100,
            },
            {
                "air_date": "2008-01-27",
                "crew": [],
                "episode_number": 2,
                "episode_type": "standard",
                "guest_stars": [
                    {"id": 20, "name": "测试客串", "character": "角色"}
                ],
                "id": 62086,
                "name": "第二集",
                "overview": "第二集简介",
                "production_code": "102",
                "runtime": 48,
                "season_number": 1,
                "show_id": 1396,
                "still_path": "/episode-2.jpg",
                "vote_average": 8.3,
                "vote_count": 101,
            },
        ]
        mocked_find_media.return_value = {
            "adult": False,
            "backdrop_path": "/series-backdrop.jpg",
            "created_by": [{"id": 1, "name": "测试创作者"}],
            "episode_run_time": [48, 58],
            "first_air_date": "2008-01-20",
            "genres": [{"id": 18, "name": "剧情"}],
            "homepage": "",
            "id": 1396,
            "in_production": False,
            "last_air_date": "2013-09-29",
            "last_episode_to_air": episodes[1],
            "name": "测试剧集",
            "networks": [{"id": 174, "name": "AMC"}],
            "next_episode_to_air": None,
            "number_of_episodes": 2,
            "number_of_seasons": 1,
            "origin_country": ["US"],
            "original_language": "en",
            "original_name": "Test Series",
            "overview": "剧集简介",
            "popularity": 100,
            "poster_path": "/series-poster.jpg",
            "production_companies": [
                {
                    "id": 300,
                    "logo_path": "/company.png",
                    "name": "测试电视公司",
                    "origin_country": "US",
                }
            ],
            "production_countries": [{"iso_3166_1": "US", "name": "美国"}],
            "seasons": [
                {
                    "air_date": "2008-01-20",
                    "episode_count": 2,
                    "id": 3572,
                    "name": "第 1 季",
                    "overview": "季简介",
                    "poster_path": "/season.jpg",
                    "season_number": 1,
                    "vote_average": 8.5,
                }
            ],
            "softcore": False,
            "spoken_languages": [
                {"english_name": "English", "iso_639_1": "en", "name": "英语"}
            ],
            "status": "Ended",
            "tagline": "",
            "type": "Scripted",
            "vote_average": 9.0,
            "vote_count": 1000,
        }
        mocked_search_tv_season.return_value = {
            "_id": "season-internal-id",
            "air_date": "2008-01-20",
            "episodes": episodes,
            "id": 3572,
            "name": "第 1 季",
            "networks": [{"id": 174, "name": "AMC"}],
            "overview": "季简介",
            "poster_path": "/season.jpg",
            "season_number": 1,
            "vote_average": 8.5,
        }
        mocked_search_media_images.return_value = {
            "posters": ["https://image.tmdb.org/tv-poster.jpg"],
            "logos": ["https://image.tmdb.org/tv-logo.png"],
            "backdrops": [],
        }
        mocked_download_media_images.return_value = [
            {
                "image_type": "posters",
                "image_position": 1,
                "image_url": "https://image.tmdb.org/tv-poster.jpg",
                "local_path": "images/tv/1396/posters/1.jpg",
            },
            {
                "image_type": "logos",
                "image_position": 1,
                "image_url": "https://image.tmdb.org/tv-logo.png",
                "local_path": "images/tv/1396/logos/1.png",
            },
        ]
        mocked_download_tv_episode_still.return_value = {
            "episode_id": 62086,
            "local_path": "images/tv/1396/season-1/episodes/62086.jpg",
        }

        form_data = {
            "media_type": "tv",
            "media_name": "测试剧集",
            "season_number": "1",
            "episode_number": "2",
            "alist_url": (
                "http://127.0.0.1:5244/d/Quark/"
                "%E6%B5%8B%E8%AF%95%E5%89%A7%E9%9B%86.S01E02.mkv?sign=test:0"
            ),
        }
        response = self.client.post("/media", data=form_data, follow_redirects=True)
        repeated_response = self.client.post(
            "/media",
            data=form_data,
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated_response.status_code, 200)
        self.assertIn("已关联剧集".encode(), response.data)
        self.assertIn("S01E02".encode(), response.data)
        self.assertIn("第二集".encode(), response.data)

        with closing(sqlite3.connect(self.database_path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM tv_series").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM tv_season").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM tv_episode").fetchone()[0], 2)
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM tv_episode_source").fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM tv_series_image").fetchone()[0],
                2,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM tv_episode_image").fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM tv_series_genre").fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(*) FROM tv_series_production_company"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(*) FROM tv_series_spoken_language"
                ).fetchone()[0],
                1,
            )
            source_episode = database.execute(
                "SELECT episode_id, alist_path FROM tv_episode_source"
            ).fetchone()
            self.assertEqual(source_episode[0], 62086)
            self.assertEqual(source_episode[1], "/Quark/测试剧集.S01E02.mkv")
            self.assertEqual(
                database.execute(
                    "SELECT last_episode_id FROM tv_series WHERE series_id = 1396"
                ).fetchone()[0],
                62086,
            )
            crew = json.loads(
                database.execute(
                    "SELECT crew_json FROM tv_episode WHERE episode_id = 62085"
                ).fetchone()[0]
            )
            self.assertEqual(crew[0]["name"], "测试导演")
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        mocked_search_media_images.assert_called_once_with("tv", 1396)
        mocked_download_media_images.assert_called_once()
        self.assertEqual(mocked_download_tv_episode_still.call_count, 2)

        movie_app.models.save_watch_progress(
            self.database_path,
            "tv",
            62086,
            300_000,
            2_880_000,
        )
        homepage_data = movie_app.models.get_homepage_data(self.database_path)
        self.assertEqual(homepage_data["continue_watching"][0]["media_id"], 1396)
        self.assertEqual(
            homepage_data["continue_watching"][0]["logo_path"],
            "images/tv/1396/logos/1.png",
        )
        self.assertEqual(
            homepage_data["continue_watching"][0]["episode_number"],
            2,
        )
        home_response = self.client.get("/")
        tv_genre_response = self.client.get(
            "/genres?media_type=tv&genre_id=18"
        )
        detail_response = self.client.get("/series/1396")
        self.assertEqual(home_response.status_code, 200)
        self.assertIn("测试剧集".encode(), home_response.data)
        self.assertIn("剧集类别".encode(), home_response.data)
        self.assertIn("测试剧集".encode(), tv_genre_response.data)
        self.assertIn(b"continue-logo position-absolute", home_response.data)
        self.assertIn(b"images/tv/1396/logos/1.png", home_response.data)
        favorite_toggle_response = self.client.post(
            "/favorites/tv/1396/toggle",
            follow_redirects=True,
        )
        self.assertIn("已收藏".encode(), favorite_toggle_response.data)
        self.assertIn(
            "测试剧集".encode(),
            self.client.get("/favorites?folder_id=2").data,
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("选集".encode(), detail_response.data)
        self.assertIn("S01E02".encode(), detail_response.data)
        self.assertNotIn("第二集简介".encode(), detail_response.data)
        self.assertIn("48 分钟".encode(), detail_response.data)
        self.assertIn("2008-01-27".encode(), detail_response.data)
        self.assertIn(b"episode-card", detail_response.data)
        self.assertIn(b"season-tabs", detail_response.data)
        self.assertNotIn(b"season-tab-overview", detail_response.data)
        self.assertIn(b"episode-rail", detail_response.data)
        self.assertIn(b"episode-rail-track container-xl", detail_response.data)
        self.assertIn("第 1 季".encode(), detail_response.data)
        self.assertIn("测试创作者".encode(), detail_response.data)
        self.assertIn("AMC".encode(), detail_response.data)
        self.assertIn("已观看 10%".encode(), detail_response.data)
        self.assertIn(b'href="/play/tv/1"', detail_response.data)
        self.assertNotIn(b'href="http://127.0.0.1:5244', detail_response.data)
        self.assertIn(b"images/tv/1396/season-1/episodes/62086.jpg", detail_response.data)
        self.assertNotIn(b"detail-poster", detail_response.data)

        movie_app.models.add_tv_episode_source(
            self.database_path,
            62086,
            "/Quark/测试剧集.S01E02.1080p.mkv",
        )
        detail_data = movie_app.models.get_media_detail(
            self.database_path,
            "tv",
            1396,
        )
        episode = detail_data["seasons"][0]["episodes"][1]
        self.assertEqual(len(episode["sources"]), 2)
        version_response = self.client.get("/series/1396")
        self.assertIn(
            'aria-label="继续播放 05:00，选择版本"'.encode(),
            version_response.data,
        )
        self.assertIn(
            'aria-label="播放 S1E2，选择版本"'.encode(),
            version_response.data,
        )
        self.assertGreaterEqual(
            version_response.data.count(b'data-bs-toggle="dropdown"'),
            2,
        )
        self.assertIn(b'href="/play/tv/1"', version_response.data)
        self.assertIn(b'href="/play/tv/2"', version_response.data)

        with (
            patch(
                "helpers.get_video_link",
                return_value="https://alist.example/raw/episode.mkv",
            ) as mocked_get_video_link,
            patch("helpers.play_video") as mocked_play_video,
        ):
            play_response = self.client.get("/play/tv/1")

        self.assertEqual(play_response.status_code, 204)
        mocked_get_video_link.assert_called_once_with(
            "/Quark/测试剧集.S01E02.mkv"
        )
        self.assertEqual(
            mocked_play_video.call_args.args,
            ("https://alist.example/raw/episode.mkv",),
        )
        self.assertEqual(
            mocked_play_video.call_args.kwargs["start_position_ms"],
            300_000,
        )
        progress_callback = mocked_play_video.call_args.kwargs["progress_callback"]
        progress_callback(600_000, 2_880_000)
        with closing(sqlite3.connect(self.database_path)) as database:
            self.assertEqual(
                database.execute(
                    "SELECT position_ms, duration_ms FROM tv_episode_watch_progress"
                ).fetchone(),
                (600_000, 2_880_000),
            )


if __name__ == "__main__":
    unittest.main()
