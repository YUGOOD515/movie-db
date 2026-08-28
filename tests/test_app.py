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

        self.assertEqual(home_response.status_code, 200)
        self.assertNotIn('action="/media"'.encode(), home_response.data)
        self.assertNotIn(b"library-heading", home_response.data)
        self.assertNotIn(b"continue-heading", home_response.data)
        for section_name in (
            "最近观看",
            "收藏夹",
            "最近添加",
            "电影类别",
            "未观看",
            "最受好评的电影",
        ):
            self.assertIn(section_name.encode(), home_response.data)
        self.assertIn("还没有观看记录".encode(), home_response.data)
        self.assertIn("media-rail--folder".encode(), home_response.data)
        self.assertIn(b"site-menu-toggle", home_response.data)
        self.assertIn(b"site-sidebar offcanvas offcanvas-start", home_response.data)
        self.assertNotIn(b"offcanvas-lg", home_response.data)
        self.assertEqual(media_response.status_code, 200)
        self.assertIn("媒体资源关联".encode(), media_response.data)
        self.assertIn('action="/media"'.encode(), media_response.data)

    def test_missing_media_detail_returns_not_found(self):
        self.assertEqual(self.client.get("/movies/999999").status_code, 404)
        self.assertEqual(self.client.get("/series/999999").status_code, 404)

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
            "/Quark/%E6%B5%8B%E8%AF%95%E7%94%B5%E5%BD%B1.mkv".encode(),
            response.data,
        )

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

        home_response = self.client.get("/")
        detail_response = self.client.get("/movies/157336")
        self.assertEqual(home_response.status_code, 200)
        self.assertIn("测试电影".encode(), home_response.data)
        self.assertIn("最近添加".encode(), home_response.data)
        self.assertIn("media-rail--poster".encode(), home_response.data)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("测试简介".encode(), detail_response.data)
        self.assertIn("播放电影".encode(), detail_response.data)
        self.assertIn(b"site-sidebar", detail_response.data)
        self.assertIn(b"detail-main", detail_response.data)
        self.assertIn("年龄分级 未提供".encode(), detail_response.data)
        self.assertNotIn(b"detail-poster", detail_response.data)

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

        home_response = self.client.get("/")
        detail_response = self.client.get("/series/1396")
        self.assertEqual(home_response.status_code, 200)
        self.assertIn("测试剧集".encode(), home_response.data)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("选集".encode(), detail_response.data)
        self.assertIn("S01E02".encode(), detail_response.data)
        self.assertIn("第二集简介".encode(), detail_response.data)
        self.assertIn("48 分钟".encode(), detail_response.data)
        self.assertIn("2008-01-27".encode(), detail_response.data)
        self.assertIn(b"episode-card", detail_response.data)
        self.assertIn(b"images/tv/1396/season-1/episodes/62086.jpg", detail_response.data)
        self.assertNotIn(b"detail-poster", detail_response.data)


if __name__ == "__main__":
    unittest.main()
