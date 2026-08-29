import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
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
        media_associate_response = self.client.get("/media?tab=associate")
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
        self.assertEqual(media_associate_response.status_code, 200)
        self.assertIn(b"media-primary-action", media_associate_response.data)
        self.assertNotIn(b"btn-primary", media_associate_response.data)
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
        self.assertGreaterEqual(
            genre_response.data.count(
                b"btn btn-sm btn-outline-secondary dropdown-toggle genre-dropdown-toggle"
            ),
            3,
        )
        self.assertIn(
            b'class="btn btn-sm genre-pill-btn" for="genre-all">',
            genre_response.data,
        )
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

    @patch("app.probe_alist_source")
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
        mocked_probe_alist_source,
    ):
        mocked_probe_alist_source.return_value = {
            "media_duration_ms": 7_200_000,
            "file_size_bytes": 4_294_967_296,
            "bit_rate": 4_772_186,
            "container_format": "matroska,webm",
            "video_codec": "hevc",
            "video_width": 3840,
            "video_height": 2160,
            "video_frame_rate": 23.976,
            "video_pixel_format": "yuv420p10le",
            "video_dynamic_range": "HDR10 / PQ",
            "audio_codec": "eac3",
            "audio_channels": 6,
            "audio_channel_layout": "5.1(side)",
        }
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
            },
            {
                "image_type": "posters",
                "image_position": 2,
                "image_url": "https://image.tmdb.org/poster-2.jpg",
                "local_path": "images/157336/posters/2.jpg",
            },
            {
                "image_type": "backdrops",
                "image_position": 1,
                "image_url": "https://image.tmdb.org/backdrop.jpg",
                "local_path": "images/157336/backdrops/1.jpg",
            },
            {
                "image_type": "backdrops",
                "image_position": 2,
                "image_url": "https://image.tmdb.org/backdrop-2.jpg",
                "local_path": "images/157336/backdrops/2.jpg",
            },
            {
                "image_type": "logos",
                "image_position": 1,
                "image_url": "https://image.tmdb.org/logo.png",
                "local_path": "images/157336/logos/1.png",
            },
            {
                "image_type": "logos",
                "image_position": 2,
                "image_url": "https://image.tmdb.org/logo-2.png",
                "local_path": "images/157336/logos/2.png",
            },
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
            source_metadata = database.execute(
                """
                SELECT media_duration_ms,
                       video_codec,
                       video_width,
                       video_height,
                       probed_at
                  FROM movie_source
                """
            ).fetchone()
            self.assertEqual(source_metadata[:4], (7_200_000, "hevc", 3840, 2160))
            self.assertIsNotNone(source_metadata[4])
            self.assertEqual(database.execute("SELECT COUNT(*) FROM movie_image").fetchone()[0], 6)
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
                database.execute(
                    """
                    SELECT local_path
                      FROM movie_image
                     WHERE image_type = 'posters'
                       AND image_position = 1
                    """
                ).fetchone()[0],
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
        self.assertIn(b"play-button btn media-primary-action", home_response.data)
        self.assertNotIn(b"play-button btn btn-dark", home_response.data)
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
        self.assertIn(b"media-primary-action detail-play", detail_response.data)
        self.assertNotIn(b"btn-primary detail-play", detail_response.data)
        self.assertIn(b'href="/play/movie/1"', detail_response.data)
        self.assertNotIn(b'href="http://127.0.0.1:5244', detail_response.data)
        self.assertIn(b"site-sidebar", detail_response.data)
        self.assertIn(b"detail-main", detail_response.data)
        self.assertIn("年龄分级".encode(), detail_response.data)
        self.assertIn("未提供".encode(), detail_response.data)
        self.assertIn("作品信息".encode(), detail_response.data)
        self.assertIn("当前播放文件".encode(), detail_response.data)
        self.assertIn("Test Movie".encode(), detail_response.data)
        self.assertIn("剧情".encode(), detail_response.data)
        self.assertIn("美国".encode(), detail_response.data)
        self.assertIn("英语".encode(), detail_response.data)
        self.assertIn("测试公司".encode(), detail_response.data)
        self.assertIn("3840 × 2160".encode(), detail_response.data)
        self.assertIn(b">4K<", detail_response.data)
        self.assertIn(b"HEVC", detail_response.data)
        self.assertIn(b"HDR10 / PQ", detail_response.data)
        self.assertIn("播放版本".encode(), detail_response.data)
        self.assertIn(b"detail-external-links", detail_response.data)
        self.assertNotIn(b"data-episode-browser", detail_response.data)
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

        management_response = self.client.get("/media?tab=overview")
        self.assertEqual(management_response.status_code, 200)
        self.assertIn("横版海报".encode(), management_response.data)
        self.assertIn("竖版海报".encode(), management_response.data)
        self.assertIn(b"data-artwork-confirm", management_response.data)
        self.assertNotIn("Logo 叠加预览".encode(), management_response.data)
        self.assertIn(b"images/157336/posters/1.jpg", management_response.data)
        self.assertIn(b"images/157336/posters/2.jpg", management_response.data)
        self.assertIn(b"overview-media-list", management_response.data)
        self.assertIn(b"artwork-backdrop-logo position-absolute", management_response.data)
        self.assertGreaterEqual(
            management_response.data.count(b"artwork-preview"),
            2,
        )
        self.assertIn(b"overview-artwork-col--backdrop", management_response.data)
        self.assertIn(b"overview-artwork-col--poster", management_response.data)

        selected_paths = {
            "backdrops": "images/157336/backdrops/2.jpg",
            "posters": "images/157336/posters/2.jpg",
            "logos": "images/157336/logos/2.png",
        }
        for image_type in selected_paths:
            selection_response = self.client.post(
                "/media/movie/157336/artwork",
                data={
                    "image_type": image_type,
                    "image_position": "2",
                    "type": "movie",
                },
                follow_redirects=True,
            )
            self.assertEqual(selection_response.status_code, 200)
            self.assertIn("已更新".encode(), selection_response.data)

        with closing(sqlite3.connect(self.database_path)) as database:
            primary_images = dict(
                database.execute(
                    """
                    SELECT image_type,
                           local_path
                      FROM movie_image
                     WHERE movie_id = 157336
                       AND image_position = 1
                    """
                ).fetchall()
            )
            self.assertEqual(primary_images, selected_paths)
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        selected_detail = movie_app.models.get_media_detail(
            self.database_path,
            "movie",
            157336,
        )
        self.assertEqual(selected_detail["backdrop_path"], selected_paths["backdrops"])
        self.assertEqual(selected_detail["poster_path"], selected_paths["posters"])
        self.assertEqual(selected_detail["logo_path"], selected_paths["logos"])

        movie_app.models.save_movie(
            self.database_path,
            mocked_find_media.return_value,
            mocked_download_media_images.return_value,
            "/Quark/测试电影.mkv",
            mocked_probe_alist_source.return_value,
        )
        refreshed_detail = movie_app.models.get_media_detail(
            self.database_path,
            "movie",
            157336,
        )
        self.assertEqual(refreshed_detail["backdrop_path"], selected_paths["backdrops"])
        self.assertEqual(refreshed_detail["poster_path"], selected_paths["posters"])
        self.assertEqual(refreshed_detail["logo_path"], selected_paths["logos"])

    @patch("app.probe_alist_source")
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
        mocked_probe_alist_source,
    ):
        mocked_probe_alist_source.return_value = {
            "media_duration_ms": 2_880_000,
            "file_size_bytes": 2_147_483_648,
            "bit_rate": 5_965_232,
            "container_format": "matroska,webm",
            "video_codec": "h264",
            "video_width": 1920,
            "video_height": 1080,
            "video_frame_rate": 24.0,
            "video_pixel_format": "yuv420p",
            "video_dynamic_range": None,
            "audio_codec": "aac",
            "audio_channels": 2,
            "audio_channel_layout": "stereo",
        }
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
            source_metadata = database.execute(
                """
                SELECT media_duration_ms,
                       video_codec,
                       video_width,
                       video_height,
                       probed_at
                  FROM tv_episode_source
                """
            ).fetchone()
            self.assertEqual(source_metadata[:4], (2_880_000, "h264", 1920, 1080))
            self.assertIsNotNone(source_metadata[4])
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
        self.assertNotIn(">选集<".encode(), detail_response.data)
        self.assertIn("S1 · E2".encode(), detail_response.data)
        self.assertIn("第二集简介".encode(), detail_response.data)
        self.assertIn("48 分钟".encode(), detail_response.data)
        self.assertIn("2008-01-27".encode(), detail_response.data)
        self.assertIn(b"episode-card", detail_response.data)
        self.assertNotIn(b"episode-play", detail_response.data)
        self.assertIn(b'data-episode-hero="62086"', detail_response.data)
        self.assertIn(b'data-episode-hero-control="62086"', detail_response.data)
        self.assertIn(b'data-episode-target="62086"', detail_response.data)
        self.assertNotIn(b"data-episode-detail", detail_response.data)
        self.assertNotIn(b"episode-feature", detail_response.data)
        self.assertNotIn(b"season-tabs", detail_response.data)
        self.assertNotIn(b"episode-grid", detail_response.data)
        self.assertIn(b"episode-rail media-rail", detail_response.data)
        self.assertIn(
            b"episode-rail-track media-rail-track container-xl",
            detail_response.data,
        )
        self.assertNotIn("选择季".encode(), detail_response.data)
        self.assertIn("第 1 季".encode(), detail_response.data)
        self.assertIn("2008".encode(), detail_response.data)
        self.assertIn("2 集".encode(), detail_response.data)
        self.assertIn("2.00 GiB".encode(), detail_response.data)
        self.assertIn("1080P".encode(), detail_response.data)
        self.assertIn(b"AAC", detail_response.data)
        self.assertIn("当前播放文件".encode(), detail_response.data)
        self.assertIn(b"detail-action-row", detail_response.data)
        self.assertIn(b"detail-play-slot", detail_response.data)
        self.assertIn(b"detail-favorite-form", detail_response.data)
        self.assertIn(b"detail-favorite", detail_response.data)
        self.assertIn(
            b"season-summary-panel d-flex flex-wrap align-items-center gap-3",
            detail_response.data,
        )
        self.assertNotIn(b"detail-hero-rating-row", detail_response.data)
        self.assertLess(
            detail_response.data.find(b'data-episode-hero-control="62086"'),
            detail_response.data.find(b"detail-favorite-form"),
        )
        self.assertIn("测试创作者".encode(), detail_response.data)
        self.assertIn("AMC".encode(), detail_response.data)
        self.assertIn("作品信息".encode(), detail_response.data)
        self.assertIn("当前播放文件".encode(), detail_response.data)
        self.assertNotIn("外部链接".encode(), detail_response.data)
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
        self.assertGreaterEqual(
            version_response.data.count(
                'aria-label="继续播放 05:00，选择版本"'.encode()
            ),
            1,
        )
        self.assertGreaterEqual(
            version_response.data.count(b'data-bs-toggle="dropdown"'),
            1,
        )
        self.assertIn(b'href="/play/tv/1"', version_response.data)
        self.assertIn(b'href="/play/tv/2"', version_response.data)
        self.assertIn(b'data-episode-facts="62086"', version_response.data)

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
            with database:
                database.execute(
                    """
                    INSERT INTO tv_series_image (
                        series_id,
                        image_type,
                        image_position,
                        image_url,
                        local_path
                    )
                         VALUES (?, 'posters', 2, ?, ?)
                    """,
                    (
                        1396,
                        "https://image.tmdb.org/tv-poster-2.jpg",
                        "images/tv/1396/posters/2.jpg",
                    ),
                )

        tv_artwork_response = self.client.post(
            "/media/tv/1396/artwork",
            data={"image_type": "posters", "image_position": "2"},
            follow_redirects=True,
        )
        self.assertEqual(tv_artwork_response.status_code, 200)
        with closing(sqlite3.connect(self.database_path)) as database:
            self.assertEqual(
                database.execute(
                    """
                    SELECT local_path
                      FROM tv_series_image
                     WHERE series_id = 1396
                       AND image_type = 'posters'
                       AND image_position = 1
                    """
                ).fetchone()[0],
                "images/tv/1396/posters/2.jpg",
            )

    @patch("helpers.subprocess.run")
    def test_probe_video_parses_real_media_parameters(self, mocked_run):
        mocked_run.return_value = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "format": {
                        "format_name": "matroska,webm",
                        "duration": "3723.456",
                        "size": "4294967296",
                        "bit_rate": "9223372",
                    },
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "hevc",
                            "codec_tag_string": "hvc1",
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
                            "channel_layout": "5.1(side)",
                        },
                    ],
                }
            ),
        )

        metadata = movie_app.helpers.probe_video(
            "https://alist.example/raw/movie.mkv"
        )

        self.assertEqual(
            metadata,
            {
                "media_duration_ms": 3_723_456,
                "file_size_bytes": 4_294_967_296,
                "bit_rate": 9_223_372,
                "container_format": "matroska,webm",
                "video_codec": "hevc",
                "video_width": 3840,
                "video_height": 2160,
                "video_frame_rate": 23.976,
                "video_pixel_format": "yuv420p10le",
                "video_dynamic_range": "HDR10 / PQ",
                "audio_codec": "eac3",
                "audio_channels": 6,
                "audio_channel_layout": "5.1(side)",
            },
        )
        command = mocked_run.call_args.args[0]
        self.assertEqual(command[0], movie_app.helpers.FFPROBE_PATH)
        self.assertEqual(command[-1], "https://alist.example/raw/movie.mkv")

    def test_detail_prefers_next_episode_and_keeps_all_populated_seasons(self):
        season_one_episode_one = {
            "source_id": 1,
            "season_number": 1,
            "episode_number": 1,
            "episode_id": 101,
            "alist_path": "/series.S01E01.mkv",
            "position_ms": 2_700_000,
            "updated_at": "2026-08-28 10:00:00",
            "is_completed": True,
            "resume_position_ms": 0,
        }
        alternate_version = {
            "source_id": 2,
            "season_number": 1,
            "episode_number": 1,
            "episode_id": 101,
            "alist_path": "/series.S01E01.1080p.mkv",
            "position_ms": 0,
            "updated_at": None,
            "is_completed": False,
            "resume_position_ms": 0,
        }
        season_one_episode_two = {
            "source_id": 3,
            "season_number": 1,
            "episode_number": 2,
            "episode_id": 102,
            "alist_path": "/series.S01E02.mkv",
            "position_ms": 0,
            "updated_at": None,
            "is_completed": False,
            "resume_position_ms": 0,
        }
        season_two_episode_one = {
            "source_id": 4,
            "season_number": 2,
            "episode_number": 1,
            "episode_id": 201,
            "alist_path": "/series.S02E01.mkv",
            "position_ms": 0,
            "updated_at": None,
            "is_completed": False,
            "resume_position_ms": 0,
        }
        detail = {
            "media_type": "tv",
            "media_id": 1,
            "sources": [
                season_one_episode_one,
                alternate_version,
                season_one_episode_two,
                season_two_episode_one,
            ],
            "seasons": [
                {
                    "season_number": 0,
                    "episodes": [],
                },
                {
                    "season_number": 1,
                    "episodes": [
                        {
                            "episode_id": 101,
                            "sources": [
                                season_one_episode_one,
                                alternate_version,
                            ],
                        },
                        {
                            "episode_id": 102,
                            "sources": [season_one_episode_two],
                        },
                    ],
                },
                {
                    "season_number": 2,
                    "episodes": [
                        {
                            "episode_id": 201,
                            "sources": [season_two_episode_one],
                        }
                    ],
                },
            ],
        }

        with movie_app.app.test_request_context():
            movie_app._prepare_detail_sources(detail, "tv")
            movie_app._prepare_detail_episodes(detail)

        self.assertEqual(detail["featured_source"]["source_id"], 3)
        self.assertEqual(detail["featured_episode"]["episode_id"], 102)
        self.assertEqual(detail["current_season"]["season_number"], 1)
        self.assertEqual(
            [season["season_number"] for season in detail["seasons"]],
            [1, 2],
        )

    def test_ratings_storage_and_constraints(self):
        # 1. Insert a movie with null ratings and verify default values
        with closing(sqlite3.connect(self.database_path)) as db:
            db.row_factory = sqlite3.Row
            db.execute(
                """
                INSERT INTO movie (
                    movie_id, adult, softcore, video, budget, revenue, popularity,
                    vote_average, vote_count, original_language, original_title,
                    title, release_status, imdb_id
                ) VALUES (
                    101, 0, 0, 0, 0, 0, 10.0, 8.5, 1000, 'en', 'Test Movie',
                    '测试电影', 'Released', 'tt1234567'
                )
                """
            )
            db.commit()

            # 2. Update ratings via save_media_ratings
            movie_app.models.save_media_ratings(
                self.database_path,
                "movie",
                101,
                {
                    "imdb_rating": 8.7,
                    "imdb_vote_count": 2500000,
                    "rotten_tomatoes_tomatometer": 93,
                    "rotten_tomatoes_popcornmeter": None,
                    "metacritic_rating": 74,
                    "ratings_updated_at": "2026-08-29 12:00:00",
                },
            )

            row = dict(
                db.execute(
                    """
                    SELECT imdb_rating,
                           imdb_vote_count,
                           rotten_tomatoes_tomatometer,
                           rotten_tomatoes_popcornmeter,
                           metacritic_rating,
                           ratings_updated_at
                      FROM movie
                     WHERE movie_id = 101
                    """
                ).fetchone()
            )
            self.assertEqual(row["imdb_rating"], 8.7)
            self.assertEqual(row["imdb_vote_count"], 2500000)
            self.assertEqual(row["rotten_tomatoes_tomatometer"], 93)
            self.assertIsNone(row["rotten_tomatoes_popcornmeter"])
            self.assertEqual(row["metacritic_rating"], 74)
            self.assertEqual(row["ratings_updated_at"], "2026-08-29 12:00:00")

            # 3. A partial refresh must not erase scores omitted by the provider.
            movie_app.models.save_media_ratings(
                self.database_path,
                "movie",
                101,
                {"imdb_rating": 8.8},
            )
            refreshed = db.execute(
                """
                SELECT imdb_rating,
                       imdb_vote_count,
                       rotten_tomatoes_tomatometer,
                       metacritic_rating
                  FROM movie
                 WHERE movie_id = 101
                """
            ).fetchone()
            self.assertEqual(refreshed["imdb_rating"], 8.8)
            self.assertEqual(refreshed["imdb_vote_count"], 2500000)
            self.assertEqual(refreshed["rotten_tomatoes_tomatometer"], 93)
            self.assertEqual(refreshed["metacritic_rating"], 74)

            # 4. Test check constraint violations on invalid ratings
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE movie SET imdb_rating = 11 WHERE movie_id = 101"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE movie SET rotten_tomatoes_tomatometer = 101 WHERE movie_id = 101"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE movie SET metacritic_rating = -1 WHERE movie_id = 101"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE movie SET imdb_vote_count = -5 WHERE movie_id = 101"
                )

            # 5. Test tv_series ratings and imdb_id
            db.execute(
                """
                INSERT INTO tv_series (
                    series_id, adult, softcore, in_production, number_of_episodes,
                    number_of_seasons, popularity, vote_average, vote_count,
                    original_language, original_name, series_name, series_status,
                    series_type, created_by_json, networks_json,
                    episode_runtime_json, imdb_id
                ) VALUES (
                    201, 0, 0, 0, 10, 1, 15.0, 9.0, 500, 'en', 'Test Series',
                    '测试剧集', 'Ended', 'Scripted', '[]', '[]', '[]', 'tt7654321'
                )
                """
            )
            db.commit()

            movie_app.models.save_media_ratings(
                self.database_path,
                "tv",
                201,
                {
                    "imdb_rating": 9.5,
                    "imdb_vote_count": 2600000,
                    "rotten_tomatoes_tomatometer": 96,
                    "rotten_tomatoes_popcornmeter": 98,
                    "metacritic_rating": 89,
                },
            )

            tv_row = dict(
                db.execute(
                    """
                    SELECT imdb_id,
                           imdb_rating,
                           imdb_vote_count,
                           rotten_tomatoes_tomatometer,
                           rotten_tomatoes_popcornmeter,
                           metacritic_rating,
                           ratings_updated_at
                      FROM tv_series
                     WHERE series_id = 201
                    """
                ).fetchone()
            )
            self.assertEqual(tv_row["imdb_id"], "tt7654321")
            self.assertEqual(tv_row["imdb_rating"], 9.5)
            self.assertEqual(tv_row["imdb_vote_count"], 2600000)
            self.assertEqual(tv_row["rotten_tomatoes_tomatometer"], 96)
            self.assertEqual(tv_row["rotten_tomatoes_popcornmeter"], 98)
            self.assertEqual(tv_row["metacritic_rating"], 89)
            self.assertIsNotNone(tv_row["ratings_updated_at"])

    def test_parse_omdb_ratings_various_payloads(self):
        # 1. Full payload
        full_payload = {
            "Response": "True",
            "imdbRating": "8.7",
            "imdbVotes": "2,516,752",
            "Metascore": "74",
            "Ratings": [
                {"Source": "Internet Movie Database", "Value": "8.7/10"},
                {"Source": "Rotten Tomatoes", "Value": "73%"},
                {"Source": "Metacritic", "Value": "74/100"},
            ],
        }
        parsed = movie_app.helpers.parse_omdb_ratings(full_payload)
        self.assertEqual(parsed["imdb_rating"], 8.7)
        self.assertEqual(parsed["imdb_vote_count"], 2516752)
        self.assertEqual(parsed["rotten_tomatoes_tomatometer"], 73)
        self.assertIsNone(parsed["rotten_tomatoes_popcornmeter"])
        self.assertEqual(parsed["metacritic_rating"], 74)

        # 2. Payload with N/A, empty strings, missing fields and custom Ratings order
        partial_payload = {
            "Response": "True",
            "imdbRating": "N/A",
            "imdbVotes": "N/A",
            "Metascore": "N/A",
            "Ratings": [
                {"Source": "Rotten Tomatoes", "Value": "95%"},
                {"Source": "Internet Movie Database", "Value": "8.2/10"},
            ],
        }
        parsed_partial = movie_app.helpers.parse_omdb_ratings(partial_payload)
        self.assertEqual(parsed_partial["imdb_rating"], 8.2)
        self.assertIsNone(parsed_partial["imdb_vote_count"])
        self.assertEqual(parsed_partial["rotten_tomatoes_tomatometer"], 95)
        self.assertIsNone(parsed_partial["rotten_tomatoes_popcornmeter"])
        self.assertIsNone(parsed_partial["metacritic_rating"])

        # 3. Response: False or empty/invalid payloads
        false_payload = {"Response": "False", "Error": "Error getting data."}
        parsed_false = movie_app.helpers.parse_omdb_ratings(false_payload)
        self.assertEqual(
            parsed_false,
            {
                "imdb_rating": None,
                "imdb_vote_count": None,
                "rotten_tomatoes_tomatometer": None,
                "rotten_tomatoes_popcornmeter": None,
                "metacritic_rating": None,
            },
        )
        self.assertEqual(
            movie_app.helpers.parse_omdb_ratings(None),
            {
                "imdb_rating": None,
                "imdb_vote_count": None,
                "rotten_tomatoes_tomatometer": None,
                "rotten_tomatoes_popcornmeter": None,
                "metacritic_rating": None,
            },
        )

    @patch("helpers.requests.get")
    def test_fetch_omdb_ratings_and_error_handling(self, mocked_get):
        # 1. Missing API key raises RatingFetchError
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.fetch_omdb_ratings("tt1234567", apikey="")

        # 2. Invalid or missing ID returns None without crashing
        self.assertIsNone(movie_app.helpers.fetch_omdb_ratings("", apikey="test_key"))
        self.assertIsNone(movie_app.helpers.fetch_omdb_ratings("invalid_id", apikey="test_key"))

        # 3. Network error / Timeout raises RatingFetchError
        mocked_get.side_effect = requests.Timeout("Connection timed out")
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.fetch_omdb_ratings("tt1234567", apikey="test_key")

        # 4. HTTP 500 error raises RatingFetchError
        mocked_get.side_effect = requests.HTTPError("500 Server Error")
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.fetch_omdb_ratings("tt1234567", apikey="test_key")

        # 5. OMDb API service limit / auth error raises RatingFetchError
        mock_limit_resp = unittest.mock.Mock()
        mock_limit_resp.raise_for_status.return_value = None
        mock_limit_resp.json.return_value = {
            "Response": "False",
            "Error": "Request limit reached!",
        }
        mocked_get.side_effect = None
        mocked_get.return_value = mock_limit_resp
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.fetch_omdb_ratings("tt1234567", apikey="test_key")

        # 6. OMDb "Movie not found!" returns None cleanly (valid response, no scores)
        mock_not_found = unittest.mock.Mock()
        mock_not_found.raise_for_status.return_value = None
        mock_not_found.json.return_value = {
            "Response": "False",
            "Error": "Movie not found!",
        }
        mocked_get.return_value = mock_not_found
        self.assertIsNone(movie_app.helpers.fetch_omdb_ratings("tt1234567", apikey="test_key"))

        # 7. Successful response
        mock_response = unittest.mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "Response": "True",
            "imdbRating": "9.0",
            "imdbVotes": "100,000",
        }
        mocked_get.return_value = mock_response

        res = movie_app.helpers.fetch_omdb_ratings("tt1234567", apikey="test_key")
        self.assertEqual(res["imdbRating"], "9.0")
        self.assertEqual(res["imdbVotes"], "100,000")

    @patch("helpers.fetch_omdb_ratings")
    @patch("helpers.get_media_external_ids")
    def test_fetch_media_ratings_retrieves_imdb_id_for_tv(
        self, mocked_get_ext, mocked_fetch_omdb
    ):
        mocked_get_ext.return_value = {"imdb_id": "tt9999999"}
        mocked_fetch_omdb.return_value = {
            "Response": "True",
            "imdbRating": "8.5",
            "imdbVotes": "50,000",
            "Ratings": [{"Source": "Rotten Tomatoes", "Value": "88%"}],
        }

        ratings = movie_app.helpers.fetch_media_ratings("tv", 12345, apikey="test_key")
        self.assertEqual(ratings["imdb_id"], "tt9999999")
        self.assertEqual(ratings["imdb_rating"], 8.5)
        self.assertEqual(ratings["imdb_vote_count"], 50000)
        self.assertEqual(ratings["rotten_tomatoes_tomatometer"], 88)
        self.assertIsNone(ratings["rotten_tomatoes_popcornmeter"])
        mocked_get_ext.assert_called_once_with("tv", 12345)
        mocked_fetch_omdb.assert_called_once_with("tt9999999", apikey="test_key")

    @patch("helpers._tmdb_get", side_effect=requests.Timeout("TMDB timed out"))
    def test_external_id_failure_is_not_converted_to_an_empty_result(self, mocked_get):
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.get_media_external_ids("tv", 12345)

        mocked_get.assert_called_once_with("/tv/12345/external_ids")

    @patch("app.helpers.fetch_media_ratings")
    @patch("app.helpers.download_media_images")
    @patch("app.helpers.search_media_images")
    @patch("app.helpers.find_media")
    @patch("app.probe_alist_source")
    def test_import_movie_saves_ratings(
        self,
        mock_probe,
        mock_find_media,
        mock_search_images,
        mock_download_images,
        mock_fetch_ratings,
    ):
        mock_probe.return_value = {
            "media_duration_ms": 7200000,
            "file_size_bytes": 1000000,
            "bit_rate": 2000000,
            "container_format": "mkv",
            "video_codec": "hevc",
            "video_width": 3840,
            "video_height": 2160,
            "video_frame_rate": 24,
            "video_pixel_format": "yuv420p10le",
            "video_dynamic_range": "HDR10",
            "audio_codec": "aac",
            "audio_channels": 2,
            "audio_channel_layout": "stereo",
        }
        mock_find_media.return_value = {
            "id": 999,
            "adult": False,
            "budget": 0,
            "genres": [],
            "homepage": "",
            "imdb_id": "tt1239999",
            "origin_country": ["US"],
            "original_language": "en",
            "original_title": "Integration Test Movie",
            "overview": "Overview",
            "popularity": 10.0,
            "production_companies": [],
            "production_countries": [],
            "release_date": "2026-08-29",
            "revenue": 0,
            "runtime": 100,
            "softcore": False,
            "spoken_languages": [],
            "status": "Released",
            "tagline": "",
            "title": "集成测试电影",
            "video": False,
            "vote_average": 8.1,
            "vote_count": 500,
        }
        mock_search_images.return_value = {"posters": [], "logos": [], "backdrops": []}
        mock_download_images.return_value = []
        mock_fetch_ratings.return_value = {
            "imdb_id": "tt1239999",
            "imdb_rating": 8.3,
            "imdb_vote_count": 12000,
            "rotten_tomatoes_tomatometer": 85,
            "rotten_tomatoes_popcornmeter": None,
            "metacritic_rating": 79,
        }

        with movie_app.app.app_context():
            msg = movie_app.import_movie("集成测试电影", "/Quark/test.mkv")
            self.assertIn("已关联电影", msg)

        with closing(sqlite3.connect(self.database_path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT imdb_id, imdb_rating, imdb_vote_count,
                       rotten_tomatoes_tomatometer, rotten_tomatoes_popcornmeter,
                       metacritic_rating, ratings_updated_at
                  FROM movie
                 WHERE movie_id = 999
                """
            ).fetchone()
            self.assertEqual(row["imdb_id"], "tt1239999")
            self.assertEqual(row["imdb_rating"], 8.3)
            self.assertEqual(row["imdb_vote_count"], 12000)
            self.assertEqual(row["rotten_tomatoes_tomatometer"], 85)
            self.assertIsNone(row["rotten_tomatoes_popcornmeter"])
            self.assertEqual(row["metacritic_rating"], 79)
            self.assertIsNotNone(row["ratings_updated_at"])

    @patch("app.helpers.fetch_media_ratings")
    @patch("app.helpers.download_media_images")
    @patch("app.helpers.search_media_images")
    @patch("app.helpers.find_media")
    @patch("app.probe_alist_source")
    def test_import_movie_preserves_ratings_on_omdb_failure_and_skips_when_cached(
        self,
        mock_probe,
        mock_find_media,
        mock_search_images,
        mock_download_images,
        mock_fetch_ratings,
    ):
        mock_probe.return_value = {
            "media_duration_ms": 7200000,
            "file_size_bytes": 1000000,
            "bit_rate": 2000000,
            "container_format": "mkv",
            "video_codec": "hevc",
            "video_width": 3840,
            "video_height": 2160,
            "video_frame_rate": 24,
            "video_pixel_format": "yuv420p10le",
            "video_dynamic_range": "HDR10",
            "audio_codec": "aac",
            "audio_channels": 2,
            "audio_channel_layout": "stereo",
        }
        mock_find_media.return_value = {
            "id": 998,
            "adult": False,
            "budget": 0,
            "genres": [],
            "homepage": "",
            "imdb_id": "tt1239998",
            "origin_country": ["US"],
            "original_language": "en",
            "original_title": "Existing Movie",
            "overview": "Overview",
            "popularity": 10.0,
            "production_companies": [],
            "production_countries": [],
            "release_date": "2026-08-29",
            "revenue": 0,
            "runtime": 100,
            "softcore": False,
            "spoken_languages": [],
            "status": "Released",
            "tagline": "",
            "title": "已评分电影",
            "video": False,
            "vote_average": 8.1,
            "vote_count": 500,
        }
        mock_search_images.return_value = {"posters": [], "logos": [], "backdrops": []}
        mock_download_images.return_value = []

        # 1. Pre-insert movie with existing ratings
        with closing(sqlite3.connect(self.database_path)) as db:
            db.execute(
                """
                INSERT INTO movie (
                    movie_id, adult, softcore, video, budget, revenue, popularity,
                    vote_average, vote_count, original_language, original_title,
                    title, release_status, imdb_id, imdb_rating, imdb_vote_count,
                    rotten_tomatoes_tomatometer, rotten_tomatoes_popcornmeter,
                    metacritic_rating, ratings_updated_at
                ) VALUES (
                    998, 0, 0, 0, 0, 0, 10.0, 8.1, 500, 'en', 'Existing Movie',
                    '已评分电影', 'Released', 'tt1239998', 8.8, 90000, 95, NULL, 88,
                    CURRENT_TIMESTAMP
                )
                """
            )
            db.commit()

        # 2. Re-importing this movie with a second source must SKIP calling fetch_media_ratings
        with movie_app.app.app_context():
            msg = movie_app.import_movie("已评分电影", "/Quark/second_source.mkv")
            self.assertIn("已关联电影", msg)
            mock_fetch_ratings.assert_not_called()

        # Check existing ratings were completely preserved
        with closing(sqlite3.connect(self.database_path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM movie WHERE movie_id = 998").fetchone()
            self.assertEqual(row["imdb_rating"], 8.8)
            self.assertEqual(row["rotten_tomatoes_tomatometer"], 95)
            self.assertEqual(row["metacritic_rating"], 88)
            self.assertIsNotNone(row["ratings_updated_at"])

        # 3. An expired cache is retried; a fetch failure must preserve the old data.
        with closing(sqlite3.connect(self.database_path)) as db:
            db.execute(
                "UPDATE movie SET ratings_updated_at = '2000-01-01 00:00:00' WHERE movie_id = 998"
            )
            db.commit()

        mock_fetch_ratings.side_effect = movie_app.helpers.RatingFetchError(
            "OMDb network timeout"
        )
        with movie_app.app.app_context():
            msg = movie_app.import_movie(
                "已评分电影",
                "/Quark/third_source.mkv",
            )
            self.assertIn("已关联电影", msg)
            mock_fetch_ratings.assert_called_once_with(
                "movie",
                998,
                imdb_id="tt1239998",
            )

        with closing(sqlite3.connect(self.database_path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM movie WHERE movie_id = 998").fetchone()
            self.assertEqual(row["imdb_rating"], 8.8)
            self.assertEqual(row["rotten_tomatoes_tomatometer"], 95)
            self.assertEqual(row["metacritic_rating"], 88)
            self.assertEqual(row["ratings_updated_at"], "2000-01-01 00:00:00")

    @patch(
        "app._sync_media_ratings",
        side_effect=sqlite3.OperationalError("ratings write failed"),
    )
    @patch("app.models.save_movie")
    @patch("app.helpers.download_media_images", return_value=[])
    @patch("app.helpers.search_media_images", return_value={})
    @patch(
        "app.helpers.find_media",
        return_value={"id": 997, "title": "数据库错误测试", "imdb_id": "tt1239997"},
    )
    @patch("app.probe_alist_source", return_value={})
    def test_import_movie_does_not_hide_rating_database_errors(
        self,
        mock_probe,
        mock_find_media,
        mock_search_images,
        mock_download_images,
        mock_save_movie,
        mock_sync_ratings,
    ):
        with movie_app.app.app_context():
            with self.assertRaises(sqlite3.OperationalError):
                movie_app.import_movie("数据库错误测试", "/Quark/db-error.mkv")

        mock_sync_ratings.assert_called_once_with(
            "movie",
            997,
            imdb_id="tt1239997",
        )

    @patch("app.helpers.fetch_media_ratings")
    @patch("app.helpers.download_tv_episode_still")
    @patch("app.helpers.get_tv_episode")
    @patch("app.helpers.search_tv_season")
    @patch("app.helpers.find_media")
    @patch("app.probe_alist_source")
    def test_import_tv_episode_ratings_reuse(
        self,
        mock_probe,
        mock_find_media,
        mock_search_season,
        mock_get_episode,
        mock_download_still,
        mock_fetch_ratings,
    ):
        mock_probe.return_value = {
            "media_duration_ms": 3600000,
            "file_size_bytes": 500000,
            "bit_rate": 1000000,
            "container_format": "mkv",
            "video_codec": "h264",
            "video_width": 1920,
            "video_height": 1080,
            "video_frame_rate": 24,
            "video_pixel_format": "yuv420p",
            "video_dynamic_range": "SDR",
            "audio_codec": "aac",
            "audio_channels": 2,
            "audio_channel_layout": "stereo",
        }
        mock_find_media.return_value = {
            "id": 888,
            "adult": False,
            "in_production": False,
            "number_of_episodes": 10,
            "number_of_seasons": 1,
            "popularity": 15.0,
            "vote_average": 8.7,
            "vote_count": 300,
            "original_language": "en",
            "original_name": "Integration Test Series",
            "name": "集成测试剧集",
            "overview": "Overview",
            "status": "Ended",
            "type": "Scripted",
            "created_by": [],
            "networks": [],
            "episode_run_time": [45],
            "genres": [],
            "spoken_languages": [],
            "production_companies": [],
            "production_countries": [],
        }
        mock_search_season.return_value = {
            "id": 1888,
            "season_number": 1,
            "name": "第 1 季",
            "overview": "",
            "air_date": "2026-01-01",
            "poster_path": None,
            "episodes": [
                {
                    "id": 88801,
                    "season_number": 1,
                    "episode_number": 1,
                    "name": "第 1 集",
                    "overview": "E01 Overview",
                    "air_date": "2026-01-01",
                    "runtime": 45,
                    "still_path": None,
                    "vote_average": 8.5,
                    "vote_count": 100,
                },
                {
                    "id": 88802,
                    "season_number": 1,
                    "episode_number": 2,
                    "name": "第 2 集",
                    "overview": "E02 Overview",
                    "air_date": "2026-01-08",
                    "runtime": 45,
                    "still_path": None,
                    "vote_average": 8.6,
                    "vote_count": 120,
                },
            ],
        }
        mock_get_episode.side_effect = [
            mock_search_season.return_value["episodes"][0],
            mock_search_season.return_value["episodes"][1],
        ]
        mock_download_still.return_value = None
        mock_fetch_ratings.return_value = {
            "imdb_id": "tt8888888",
            "imdb_rating": 8.9,
            "imdb_vote_count": 45000,
            "rotten_tomatoes_tomatometer": 92,
            "rotten_tomatoes_popcornmeter": None,
            "metacritic_rating": 84,
        }

        with movie_app.app.app_context():
            # 1. Import Episode 1
            msg1 = movie_app.import_tv_episode("集成测试剧集", 1, 1, "/Quark/S01E01.mkv")
            self.assertIn("S01E01", msg1)
            self.assertEqual(mock_fetch_ratings.call_count, 1)

            # Check DB has ratings
            self.assertTrue(movie_app.models.has_series_ratings(self.database_path, 888))

            # 2. Import Episode 2 (should reuse ratings and NOT call fetch_media_ratings again)
            msg2 = movie_app.import_tv_episode("集成测试剧集", 1, 2, "/Quark/S01E02.mkv")
            self.assertIn("S01E02", msg2)
            self.assertEqual(mock_fetch_ratings.call_count, 1)

    @patch("app.helpers.fetch_media_ratings")
    @patch("app.helpers.download_tv_episode_still")
    @patch("app.helpers.get_tv_episode")
    @patch("app.helpers.search_tv_season")
    @patch("app.helpers.find_media")
    @patch("app.probe_alist_source")
    def test_import_tv_episode_retries_on_next_episode_if_first_failed(
        self,
        mock_probe,
        mock_find_media,
        mock_search_season,
        mock_get_episode,
        mock_download_still,
        mock_fetch_ratings,
    ):
        mock_probe.return_value = {
            "media_duration_ms": 3600000,
            "file_size_bytes": 500000,
            "bit_rate": 1000000,
            "container_format": "mkv",
            "video_codec": "h264",
            "video_width": 1920,
            "video_height": 1080,
            "video_frame_rate": 24,
            "video_pixel_format": "yuv420p",
            "video_dynamic_range": "SDR",
            "audio_codec": "aac",
            "audio_channels": 2,
            "audio_channel_layout": "stereo",
        }
        mock_find_media.return_value = {
            "id": 887,
            "adult": False,
            "in_production": False,
            "number_of_episodes": 10,
            "number_of_seasons": 1,
            "popularity": 15.0,
            "vote_average": 8.7,
            "vote_count": 300,
            "original_language": "en",
            "original_name": "Retry Test Series",
            "name": "重试测试剧集",
            "overview": "Overview",
            "status": "Ended",
            "type": "Scripted",
            "created_by": [],
            "networks": [],
            "episode_run_time": [45],
            "genres": [],
            "spoken_languages": [],
            "production_companies": [],
            "production_countries": [],
        }
        mock_search_season.return_value = {
            "id": 1887,
            "season_number": 1,
            "name": "第 1 季",
            "overview": "",
            "air_date": "2026-01-01",
            "poster_path": None,
            "episodes": [
                {
                    "id": 88701,
                    "season_number": 1,
                    "episode_number": 1,
                    "name": "第 1 集",
                    "overview": "E01 Overview",
                    "air_date": "2026-01-01",
                    "runtime": 45,
                    "still_path": None,
                    "vote_average": 8.5,
                    "vote_count": 100,
                },
                {
                    "id": 88702,
                    "season_number": 1,
                    "episode_number": 2,
                    "name": "第 2 集",
                    "overview": "E02 Overview",
                    "air_date": "2026-01-08",
                    "runtime": 45,
                    "still_path": None,
                    "vote_average": 8.6,
                    "vote_count": 120,
                },
            ],
        }
        mock_get_episode.side_effect = [
            mock_search_season.return_value["episodes"][0],
            mock_search_season.return_value["episodes"][1],
        ]
        mock_download_still.return_value = None

        # Episode 1 fails with RatingFetchError
        mock_fetch_ratings.side_effect = [
            movie_app.helpers.RatingFetchError("OMDb network timeout"),
            {
                "imdb_id": "tt8878877",
                "imdb_rating": 8.7,
                "imdb_vote_count": 30000,
                "rotten_tomatoes_tomatometer": 90,
                "rotten_tomatoes_popcornmeter": None,
                "metacritic_rating": 80,
            },
        ]

        with movie_app.app.app_context():
            # 1. Import Episode 1 (rating failed, series has_series_ratings must remain False)
            msg1 = movie_app.import_tv_episode("重试测试剧集", 1, 1, "/Quark/S01E01.mkv")
            self.assertIn("S01E01", msg1)
            self.assertFalse(movie_app.models.has_series_ratings(self.database_path, 887))

            # 2. Import Episode 2 (should RETRY and succeed)
            msg2 = movie_app.import_tv_episode("重试测试剧集", 1, 2, "/Quark/S01E02.mkv")
            self.assertIn("S01E02", msg2)
            self.assertTrue(movie_app.models.has_series_ratings(self.database_path, 887))
            self.assertEqual(mock_fetch_ratings.call_count, 2)

        with closing(sqlite3.connect(self.database_path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM tv_series WHERE series_id = 887").fetchone()
            self.assertEqual(row["imdb_rating"], 8.7)
            self.assertEqual(row["rotten_tomatoes_tomatometer"], 90)
            self.assertEqual(row["metacritic_rating"], 80)
            self.assertIsNotNone(row["ratings_updated_at"])

    @patch("helpers.requests.get")
    def test_fetch_omdb_season_episodes(self, mocked_get):
        # 1. Missing API key
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.fetch_omdb_season_episodes("tt1870479", 1, apikey="")

        # 2. Invalid or missing ID returns []
        self.assertEqual(movie_app.helpers.fetch_omdb_season_episodes("", 1, apikey="test_key"), [])
        self.assertEqual(movie_app.helpers.fetch_omdb_season_episodes("invalid_id", 1, apikey="test_key"), [])

        # 3. Network error / Timeout raises RatingFetchError
        mocked_get.side_effect = requests.Timeout("Connection timed out")
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.fetch_omdb_season_episodes("tt1870479", 1, apikey="test_key")

        # 4. Service error (e.g. rate limit) raises RatingFetchError
        mock_limit = unittest.mock.Mock()
        mock_limit.raise_for_status.return_value = None
        mock_limit.json.return_value = {"Response": "False", "Error": "Request limit reached!"}
        mocked_get.side_effect = None
        mocked_get.return_value = mock_limit
        with self.assertRaises(movie_app.helpers.RatingFetchError):
            movie_app.helpers.fetch_omdb_season_episodes("tt1870479", 1, apikey="test_key")

        # 5. Season not found returns [] cleanly
        mock_not_found = unittest.mock.Mock()
        mock_not_found.raise_for_status.return_value = None
        mock_not_found.json.return_value = {"Response": "False", "Error": "Series or season not found!"}
        mocked_get.return_value = mock_not_found
        self.assertEqual(movie_app.helpers.fetch_omdb_season_episodes("tt1870479", 99, apikey="test_key"), [])

        # 6. Successful response with mixed "N/A" and valid ratings
        mock_success = unittest.mock.Mock()
        mock_success.raise_for_status.return_value = None
        mock_success.json.return_value = {
            "Title": "Test Show",
            "Season": "1",
            "Episodes": [
                {"Title": "Ep 1", "Episode": "1", "imdbRating": "N/A", "imdbID": "tt101"},
                {"Title": "Ep 2", "Episode": "2", "imdbRating": "8.8", "imdbID": "tt102"},
                {"Title": "Ep 3", "Episode": "3", "imdbRating": "", "imdbID": None},
            ],
            "Response": "True",
        }
        mocked_get.return_value = mock_success
        res = movie_app.helpers.fetch_omdb_season_episodes("tt1870479", 1, apikey="test_key")
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0], {"episode_number": 1, "imdb_id": "tt101", "imdb_rating": None})
        self.assertEqual(res[1], {"episode_number": 2, "imdb_id": "tt102", "imdb_rating": 8.8})
        self.assertEqual(res[2], {"episode_number": 3, "imdb_id": None, "imdb_rating": None})

    @patch("app.helpers.fetch_omdb_season_episodes")
    @patch("app.helpers.fetch_media_ratings")
    @patch("app.helpers.download_tv_episode_still")
    @patch("app.helpers.get_tv_episode")
    @patch("app.helpers.search_tv_season")
    @patch("app.helpers.find_media")
    @patch("app.probe_alist_source")
    def test_import_tv_episode_season_imdb_ratings_pipeline(
        self,
        mock_probe,
        mock_find_media,
        mock_search_season,
        mock_get_episode,
        mock_download_still,
        mock_fetch_series_ratings,
        mock_fetch_season_episodes,
    ):
        mock_probe.return_value = {
            "media_duration_ms": 3600000,
            "file_size_bytes": 500000,
            "bit_rate": 1000000,
            "container_format": "mkv",
            "video_codec": "h264",
            "video_width": 1920,
            "video_height": 1080,
            "video_frame_rate": 24,
            "video_pixel_format": "yuv420p",
            "video_dynamic_range": "SDR",
            "audio_codec": "aac",
            "audio_channels": 2,
            "audio_channel_layout": "stereo",
        }
        mock_find_media.return_value = {
            "id": 666,
            "adult": False,
            "in_production": False,
            "number_of_episodes": 10,
            "number_of_seasons": 1,
            "popularity": 15.0,
            "vote_average": 8.7,
            "vote_count": 300,
            "original_language": "en",
            "original_name": "Season Test Series",
            "name": "季单集评分测试剧集",
            "overview": "Overview",
            "status": "Ended",
            "type": "Scripted",
            "created_by": [],
            "networks": [],
            "episode_run_time": [45],
            "genres": [],
            "spoken_languages": [],
            "production_companies": [],
            "production_countries": [],
        }
        mock_search_season.return_value = {
            "id": 1666,
            "season_number": 1,
            "name": "第 1 季",
            "overview": "",
            "air_date": "2026-01-01",
            "poster_path": None,
            "episodes": [
                {
                    "id": 66601,
                    "season_number": 1,
                    "episode_number": 1,
                    "name": "第 1 集",
                    "overview": "E01 Overview",
                    "air_date": "2026-01-01",
                    "runtime": 45,
                    "still_path": None,
                    "vote_average": 8.1,
                    "vote_count": 100,
                },
                {
                    "id": 66602,
                    "season_number": 1,
                    "episode_number": 2,
                    "name": "第 2 集",
                    "overview": "E02 Overview",
                    "air_date": "2026-01-08",
                    "runtime": 45,
                    "still_path": None,
                    "vote_average": 8.4,
                    "vote_count": 120,
                },
            ],
        }
        mock_get_episode.side_effect = [
            mock_search_season.return_value["episodes"][0],
            mock_search_season.return_value["episodes"][1],
        ]
        mock_download_still.return_value = None
        mock_fetch_series_ratings.return_value = {
            "imdb_id": "tt6666666",
            "imdb_rating": 8.9,
            "imdb_vote_count": 45000,
            "rotten_tomatoes_tomatometer": 92,
            "rotten_tomatoes_popcornmeter": None,
            "metacritic_rating": 84,
        }
        mock_fetch_season_episodes.side_effect = [
            [
                {"episode_number": 1, "imdb_id": "tt66601", "imdb_rating": 8.5},
            ],
            [
                {"episode_number": 1, "imdb_id": "tt66601", "imdb_rating": 8.5},
                {"episode_number": 2, "imdb_id": "tt66602", "imdb_rating": 9.1},
            ],
        ]

        with movie_app.app.app_context():
            # 1. Import Episode 1
            msg1 = movie_app.import_tv_episode("季单集评分测试剧集", 1, 1, "/Quark/S01E01.mkv")
            self.assertIn("S01E01", msg1)
            self.assertEqual(mock_fetch_season_episodes.call_count, 1)
            mock_fetch_season_episodes.assert_called_with("tt6666666", 1)

            # A partial OMDb response must not mark the whole season as complete.
            self.assertFalse(movie_app.models.has_season_episode_ratings(self.database_path, 666, 1))
            with closing(sqlite3.connect(self.database_path)) as db:
                db.row_factory = sqlite3.Row
                ep1 = db.execute("SELECT * FROM tv_episode WHERE episode_id = 66601").fetchone()
                ep2 = db.execute("SELECT * FROM tv_episode WHERE episode_id = 66602").fetchone()
                self.assertEqual(ep1["imdb_id"], "tt66601")
                self.assertEqual(ep1["imdb_rating"], 8.5)
                self.assertEqual(ep1["vote_average"], 8.1)
                self.assertIsNone(ep2["imdb_id"])
                self.assertIsNone(ep2["imdb_rating"])
                self.assertIsNone(ep2["imdb_rating_updated_at"])
                self.assertEqual(ep2["vote_average"], 8.4)

            # 2. Import Episode 2; the incomplete season must be fetched again.
            msg2 = movie_app.import_tv_episode("季单集评分测试剧集", 1, 2, "/Quark/S01E02.mkv")
            self.assertIn("S01E02", msg2)
            self.assertEqual(mock_fetch_season_episodes.call_count, 2)
            self.assertTrue(movie_app.models.has_season_episode_ratings(self.database_path, 666, 1))

            with closing(sqlite3.connect(self.database_path)) as db:
                db.row_factory = sqlite3.Row
                ep2 = db.execute("SELECT * FROM tv_episode WHERE episode_id = 66602").fetchone()
                self.assertEqual(ep2["imdb_id"], "tt66602")
                self.assertEqual(ep2["imdb_rating"], 9.1)

    def test_detail_page_renders_ratings_cleanly(self):
        # Insert movie with ratings
        with closing(sqlite3.connect(self.database_path)) as db:
            db.execute(
                """
                INSERT INTO movie (
                    movie_id, adult, softcore, video, budget, revenue, popularity,
                    vote_average, vote_count, original_language, original_title,
                    title, release_status, imdb_id, imdb_rating, imdb_vote_count,
                    rotten_tomatoes_tomatometer, rotten_tomatoes_popcornmeter,
                    metacritic_rating, ratings_updated_at
                ) VALUES (
                    777, 0, 0, 0, 0, 0, 10.0, 8.5, 1000, 'en', 'Detail Movie',
                    '详情页评分测试电影', 'Released', 'tt7777777', 8.8, 50000, 94, NULL, 86,
                    '2026-08-29 12:00:00'
                )
                """
            )
            # Insert TV series with season and episode ratings
            db.execute(
                """
                INSERT INTO tv_series (
                    series_id, adult, softcore, in_production, number_of_episodes,
                    number_of_seasons, popularity, vote_average, vote_count,
                    original_language, original_name, series_name, series_status,
                    series_type, created_by_json, networks_json, episode_runtime_json,
                    imdb_id, imdb_rating, imdb_vote_count, rotten_tomatoes_tomatometer,
                    metacritic_rating, ratings_updated_at
                ) VALUES (
                    888, 0, 0, 0, 2, 1, 15.0, 8.7, 300, 'en', 'Detail Series',
                    '详情页剧集测试', 'Ended', 'Scripted', '[]', '[]', '[45]',
                    'tt8888888', 8.9, 45000, 92, 84, '2026-08-29 12:00:00'
                )
                """
            )
            db.execute(
                """
                INSERT INTO tv_season (
                    season_id, series_id, season_number, episode_count, season_name,
                    vote_average, networks_json
                ) VALUES (
                    1888, 888, 1, 2, '第 1 季', 8.6, '[]'
                )
                """
            )
            db.execute(
                """
                INSERT INTO tv_episode (
                    episode_id, season_id, episode_number, episode_name, overview,
                    vote_average, vote_count, crew_json, guest_stars_json,
                    imdb_id, imdb_rating, imdb_rating_updated_at
                ) VALUES (
                    88801, 1888, 1, '第 1 集', 'E01 Overview', 8.2, 100, '[]', '[]',
                    'tt88801', 9.3, '2026-08-29 12:00:00'
                ), (
                    88802, 1888, 2, '第 2 集', 'E02 Overview', 8.5, 120, '[]', '[]',
                    NULL, NULL, '2026-08-29 12:00:00'
                )
                """
            )
            db.commit()

        # Check Movie Detail
        response = self.client.get("/movies/777")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("imdb.png", html)
        self.assertIn("8.8", html)
        self.assertIn("rotten-tomatoes.png", html)
        self.assertIn("94%", html)
        self.assertIn("metacritic.png", html)
        self.assertIn("86", html)
        self.assertIn("tmdb.png", html)
        self.assertIn("8.5", html)

        # Check TV Series Detail
        tv_response = self.client.get("/series/888")
        self.assertEqual(tv_response.status_code, 200)
        tv_html = tv_response.get_data(as_text=True)
        # Episode 1 has IMDb 9.3 and TMDB 8.2
        self.assertIn("9.3", tv_html)
        self.assertIn("8.2", tv_html)
        # Season summary toolbar has Series IMDb 8.9 and Season TMDB 8.6
        self.assertIn("8.9", tv_html)
        self.assertIn("8.6", tv_html)


if __name__ == "__main__":
    unittest.main()
