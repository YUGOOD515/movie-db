import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import requests

from connectors import tmdb
from viewmodels.title_search import build_search_viewmodel


class TitleSearchTest(unittest.TestCase):
    def setUp(self):
        tmdb._cache.clear()
        self.addCleanup(tmdb._cache.clear)

    def test_concurrent_searches_share_one_request_and_expire(self):
        entered, release = Event(), Event()

        def fetch(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return {"page": 1, "results": []}

        with patch.object(tmdb, "_session"), patch.object(tmdb, "_get", side_effect=fetch) as get:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(tmdb.search_media, "test", "movie")
                self.assertTrue(entered.wait(3))
                second = pool.submit(tmdb.search_media, "test", "movie")
                release.set()
                self.assertEqual(first.result(), second.result())
            self.assertEqual(get.call_count, 1)
            with patch.object(tmdb, "monotonic", return_value=float("inf")):
                tmdb.search_media("test", "movie")
            self.assertEqual(get.call_count, 2)

    def test_failed_requests_are_retried_and_cache_is_bounded(self):
        with patch.object(tmdb, "_session"), patch.object(tmdb, "_get", side_effect=[requests.Timeout(), {"results": []}]) as get:
            with self.assertRaises(requests.Timeout):
                tmdb.search_media("test", "tv")
            self.assertEqual(tmdb.search_media("test", "tv"), {"results": []})
            self.assertEqual(get.call_count, 2)
        for i in range(tmdb.CACHE_LIMIT + 2):
            tmdb._remember(("test", i), {})
        self.assertEqual(len(tmdb._cache), tmdb.CACHE_LIMIT)

    def test_selected_identity_skips_find_during_metadata_import(self):
        tmdb.remember_media_identity("tt1234567", "movie", 42)
        with patch.object(tmdb, "fetch_detail", return_value={"id": 42}) as detail, patch.object(tmdb, "_get") as get:
            metadata = tmdb.build_media_metadata("tt1234567", session=Mock())
        self.assertEqual(metadata["title_basic"]["tmdb_id"], 42)
        self.assertEqual(detail.call_count, 1)
        get.assert_not_called()

    def test_tv_search_uses_tv_fields_and_tolerates_missing_artwork(self):
        data = build_search_viewmodel({"page": 1, "total_pages": 1, "results": [{
            "id": 3, "name": "剧集", "original_name": "Series", "first_air_date": "2020-01-01",
        }]}, "tv")
        item = data["items"][0]
        self.assertEqual((item["name"], item["year"]), ("剧集", "2020"))
        self.assertIsNone(item["poster_url"])
        self.assertFalse(data["has_more"])
