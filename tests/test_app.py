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

    def test_rejects_non_local_alist_url(self):
        response = self.client.post(
            "/",
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

        records = movie_app.helpers.download_movie_images(
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

        records = movie_app.helpers.download_movie_images(
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

    @patch("helpers.download_movie_images")
    @patch("helpers.validate_alist_video_url")
    @patch("helpers.search_img")
    @patch("helpers.search_id")
    @patch("helpers.search_movie")
    def test_imports_and_lists_movie_mapping(
        self,
        mocked_search_movie,
        mocked_search_id,
        mocked_search_img,
        mocked_validate_alist_video_url,
        mocked_download_movie_images,
    ):
        mocked_search_movie.return_value = {"results": [{"id": 157336}]}
        mocked_search_id.return_value = {
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
        mocked_search_img.return_value = {
            "posters": ["https://image.tmdb.org/poster.jpg"],
            "logos": [],
            "backdrops": [],
        }
        mocked_download_movie_images.return_value = [
            {
                "image_type": "posters",
                "image_position": 1,
                "image_url": "https://image.tmdb.org/poster.jpg",
                "local_path": "images/157336/posters/1.jpg",
            }
        ]

        response = self.client.post(
            "/",
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
        self.assertIn("/Quark/测试电影.mkv".encode(), response.data)

        repeated_response = self.client.post(
            "/",
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


if __name__ == "__main__":
    unittest.main()
