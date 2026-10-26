import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0"}):
    import app as movie_app

sys.path.insert(0, str(movie_app.BASE_DIR / "scripts"))
import publish_snapshot  # noqa: E402

ROUTER = movie_app.BASE_DIR / "cloud" / "router" / "src" / "index.js"
SAMPLE_URLS = [
    "/",
    "/catalog?type=movie&sort=&q=%E7%99%BD%E8%89%B2%20album",
    "/catalog?b=2&a=1&a=0",
    "/title/tt0903747?season=1&x=a%2Bb%26c%27",
]


class PageKeyTest(unittest.TestCase):
    def test_drops_empty_values_and_sorts(self):
        self.assertEqual(
            publish_snapshot.page_key("/catalog?type=movie&sort=&q=a b"),
            "page/catalog?q=a%20b&type=movie",
        )
        self.assertEqual(publish_snapshot.page_key("/"), "page/")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_matches_cloud_router(self):
        script = (
            f"const {{ pageKey }} = await import({json.dumps(ROUTER.as_uri())});"
            f"const urls = {json.dumps(SAMPLE_URLS)};"
            "console.log(JSON.stringify(urls.map((u) => pageKey(new URL(u, 'https://x')))));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True, text=True, check=True,
        )
        expected = [publish_snapshot.page_key(url) for url in SAMPLE_URLS]
        self.assertEqual(json.loads(result.stdout), expected)


class ReadOnlyModeTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "library.db"
        base = movie_app.BASE_DIR
        with closing(sqlite3.connect(database_path)) as database:
            database.executescript((base / "migrations" / "library_schema.sql").read_text(encoding="utf-8"))
            database.executescript((base / "tests" / "fixtures" / "library_fixture.sql").read_text(encoding="utf-8"))
        self.previous = {key: movie_app.app.config[key] for key in ("DATABASE", "READ_ONLY", "TESTING")}
        movie_app.app.config.update(DATABASE=database_path, READ_ONLY=True, TESTING=True)
        self.client = movie_app.app.test_client()

    def tearDown(self):
        movie_app.app.config.update(self.previous)
        self.temporary_directory.cleanup()

    def test_pages_render_with_read_only_badge(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-MovieDB-Origin"], "snapshot")
        self.assertIn("只读快照", response.get_data(as_text=True))

    def test_writes_are_rejected(self):
        response = self.client.post("/manager/import/batch", json={})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.get_json()["read_only"])
        response = self.client.post("/manager/import/batch", headers={"Accept": "text/html"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("只读", response.get_data(as_text=True))

    def test_local_mode_marks_origin(self):
        movie_app.app.config["READ_ONLY"] = False
        response = self.client.get("/")
        self.assertEqual(response.headers["X-MovieDB-Origin"], "local")
        self.assertNotIn("read-only-badge", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
