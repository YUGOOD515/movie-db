import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from services import image_tones

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_TONES_DISABLED": "1"}):
    import app as movie_app

BASE83 = set(image_tones.BASE83)


def blurhash_size(value):
    flag = image_tones.BASE83.index(value[0])
    return flag % 9 + 1, flag // 9 + 1


class ImageToneTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.static_root = Path(self.temp_dir.name) / "static"
        backdrops = self.static_root / "images" / "backdrops"
        backdrops.mkdir(parents=True)
        self.backdrop = "images/backdrops/hero.jpg"
        image = Image.new("RGB", (192, 108), (20, 60, 140))
        image.paste((220, 120, 30), (96, 0, 192, 108))
        image.save(self.static_root / self.backdrop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_tone_returns_valid_blurhash_and_hex_color(self):
        tone = image_tones.compute_tone(self.static_root / self.backdrop)
        self.assertRegex(tone["color"], r"^#[0-9a-f]{6}$")
        blurhash = tone["blurhash"]
        self.assertTrue(set(blurhash) <= BASE83)
        x, y = blurhash_size(blurhash)
        self.assertEqual((x, y), (4, 3))
        self.assertEqual(len(blurhash), 4 + 2 * x * y)

    def test_only_posters_and_backdrops_get_tones(self):
        self.assertTrue(image_tones.wants_tone("images/posters/a.jpg"))
        self.assertTrue(image_tones.wants_tone(self.backdrop))
        self.assertFalse(image_tones.wants_tone("images/logos/a.png"))
        self.assertFalse(image_tones.wants_tone("../images/posters/a.jpg"))
        self.assertFalse(image_tones.wants_tone(None))

    def test_ensure_tones_writes_once_and_refreshes_on_change(self):
        paths = [self.backdrop, "images/logos/x.png", "images/posters/missing.jpg"]
        self.assertEqual(image_tones.ensure_tones(paths, self.static_root), 1)
        self.assertEqual(image_tones.ensure_tones(paths, self.static_root), 0)
        tones = image_tones.load_tones(self.static_root)
        self.assertEqual(set(tones), {self.backdrop})

        source = self.static_root / self.backdrop
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual(image_tones.ensure_tones(paths, self.static_root), 1)

        public = image_tones.public_tones(self.static_root)
        self.assertEqual(public[self.backdrop], [tones[self.backdrop]["color"], tones[self.backdrop]["blurhash"]])

    def test_image_tone_filter_reads_snapshot_config(self):
        previous = movie_app.app.config["IMAGE_TONES"]
        movie_app.app.config["IMAGE_TONES"] = {self.backdrop: ["#123456", "LEHV6nWB2yk8"]}
        try:
            with movie_app.app.test_request_context("/"):
                self.assertEqual(
                    movie_app.image_tone(self.backdrop),
                    {"color": "#123456", "blurhash": "LEHV6nWB2yk8"},
                )
                self.assertIsNone(movie_app.image_tone("images/posters/none.jpg"))
                self.assertIsNone(movie_app.image_tone(None))
                html = movie_app.app.jinja_env.from_string(
                    '{% from "_ui.html" import tone_attrs %}<div{{ tone_attrs(path) }}></div>'
                ).render(path=self.backdrop)
        finally:
            movie_app.app.config["IMAGE_TONES"] = previous
        self.assertIn("--tone: #123456", html)
        self.assertIn('data-blurhash="LEHV6nWB2yk8"', html)


if __name__ == "__main__":
    unittest.main()
