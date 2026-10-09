import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from connectors import mdblist
from scripts.local_test_api import create_app


class LocalTestApiTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        resources = {
            "tv/1396": {"id": 1396, "name": "绝命毒师"},
            "tv/1396/images": {"posters": [{"file_path": "/poster.jpg"}]},
            "tv/1396/season/1": {"season_number": 1, "episodes": [{"id": 1}]},
        }
        files = {
            "manifest.json": {"imdb_id": "tt0903747", "captured_at": "snapshot", "images": {"w185/poster.jpg": "poster.jpg"}},
            "tmdb.json": {"search": {"results": [{"id": 1396}]}, "resources": resources,
                          "find": {"tt0903747": {"tv_results": [{"id": 1396}]}}},
            "mdblist.json": {"title": "Breaking Bad", "ratings": [{"source": "imdb", "value": 9.5}]},
        }
        for name, data in files.items():
            (self.root / name).write_text(json.dumps(data), encoding="utf-8")
        (self.root / "poster.jpg").write_bytes(b"saved image")
        self.client = create_app(self.root).test_client()

    def test_search_filters_titles_and_handles_pages(self):
        for query in ("绝命毒师", "絕命", "breaking bad", "Breaking"):
            result = self.client.get("/3/search/tv", query_string={"query": query}).json
            self.assertEqual(result["results"], [{"id": 1396}])
        for url in ("/3/search/tv?query=unknown", "/3/search/tv?query=Breaking&page=2", "/3/search/movie?query=Breaking"):
            self.assertEqual(self.client.get(url).json["results"], [])

    def test_appends_and_unknown_resources_never_fetch_remote(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("Remote request")):
            result = self.client.get("/3/tv/1396?append_to_response=images,season/1").json
            self.assertEqual(result["images"]["posters"][0]["file_path"], "/poster.jpg")
            self.assertEqual(result["season/1"]["episodes"], [{"id": 1}])
            self.assertNotIn("images", self.client.get("/3/tv/1396").json)
            self.assertEqual(self.client.get("/3/tv/999").status_code, 404)
            self.assertEqual(self.client.get("/3/tv/1396?append_to_response=unknown").status_code, 404)
            self.assertEqual(self.client.get("/3/tv/1396?language=en-US").status_code, 404)
            self.assertEqual(self.client.get("/3/find/tt999").json["tv_results"], [])
            self.assertEqual(self.client.get("/mdblist/api/?i=tt999").status_code, 404)
            self.assertEqual(self.client.get("/t/p/original/unknown.jpg").status_code, 404)
            with self.client.get("/t/p/w185/poster.jpg") as response:
                self.assertEqual(response.data, b"saved image")
            self.assertEqual(self.client.get("/mdblist/api/?i=tt0903747").json["title"], "Breaking Bad")

    def test_mdblist_connector_uses_configured_url(self):
        config = mdblist.create_config({"MDBLIST_APIKEY": "local-test", "MDBLIST_API_URL": "http://127.0.0.1:5001/mdblist/api/"})
        response = Mock(status_code=200)
        response.json.return_value = self.client.get("/mdblist/api/?i=tt0903747").json
        with patch("connectors.mdblist.requests.get", return_value=response) as get:
            self.assertEqual(mdblist.fetch_title_ratings("tt0903747", config).status, "success")
        self.assertEqual(get.call_args.args[0], config.api_url)
        self.assertEqual(get.call_args.kwargs["params"], {"apikey": "local-test", "i": "tt0903747"})


if __name__ == "__main__":
    unittest.main()
