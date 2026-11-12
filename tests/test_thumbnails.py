import os
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from services.thumbnails import (
    STILL_SIZE,
    BACKDROP_CARD_SIZE,
    LOGO_CARD_SIZE,
    get_target_size_for_path,
    get_card_image_path,
    generate_thumbnail,
    ensure_thumbnail,
    shrink_original,
)


class ThumbnailServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.static_root = Path(self.temp_dir.name) / "static"
        self.images_dir = self.static_root / "images"
        self.images_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_target_size_resolution(self):
        self.assertEqual(get_target_size_for_path("images/tt123/tt456-still.jpg"), STILL_SIZE)
        self.assertEqual(get_target_size_for_path("images/tt123/backdrops/1.jpg"), BACKDROP_CARD_SIZE)
        self.assertEqual(get_target_size_for_path("images/tt123/logos/1.png"), LOGO_CARD_SIZE)
        self.assertIsNone(get_target_size_for_path("images/tt123/posters/1.jpg"))
        self.assertIsNone(get_target_size_for_path("images/tt123/hero.jpg"))

    def test_generate_and_ensure_thumbnail_for_still(self):
        # Create a dummy 4K source image
        still_dir = self.images_dir / "tt1870479"
        still_dir.mkdir()
        source_path = still_dir / "tt1870479-still.jpg"

        img = Image.new("RGB", (3840, 2160), color=(100, 150, 200))
        img.save(source_path, "JPEG")

        local_path = "images/tt1870479/tt1870479-still.jpg"

        # Before generation, get_card_image_path returns original local_path
        self.assertEqual(get_card_image_path(local_path, static_root=self.static_root), local_path)

        # Ensure thumbnail generates it
        resolved = ensure_thumbnail(local_path, static_root=self.static_root)
        self.assertEqual(resolved, f"thumbnails/{local_path}")

        # Verify thumbnail file exists and has correct dimensions
        thumb_file = self.static_root / "thumbnails" / local_path
        self.assertTrue(thumb_file.is_file())
        with Image.open(thumb_file) as thumb_img:
            self.assertEqual(thumb_img.size, STILL_SIZE)
            self.assertEqual(thumb_img.format, "JPEG")

        # Now get_card_image_path returns thumbnail path
        self.assertEqual(get_card_image_path(local_path, static_root=self.static_root), f"thumbnails/{local_path}")

    def test_cache_invalidation_when_source_is_modified(self):
        still_dir = self.images_dir / "tt1870479"
        still_dir.mkdir()
        source_path = still_dir / "test-still.jpg"

        img = Image.new("RGB", (1920, 1080), color=(255, 0, 0))
        img.save(source_path, "JPEG")
        local_path = "images/tt1870479/test-still.jpg"

        ensure_thumbnail(local_path, static_root=self.static_root)
        thumb_file = self.static_root / "thumbnails" / local_path
        self.assertTrue(thumb_file.is_file())

        # Set thumbnail mtime to be in the past to simulate stale thumbnail
        past_time = source_path.stat().st_mtime - 10.0
        os.utime(thumb_file, (past_time, past_time))

        # Before re-generation, get_card_image_path detects stale cache and falls back to original
        self.assertEqual(get_card_image_path(local_path, static_root=self.static_root), local_path)

        # ensure_thumbnail detects stale cache and regenerates
        resolved = ensure_thumbnail(local_path, static_root=self.static_root)
        self.assertEqual(resolved, f"thumbnails/{local_path}")
        self.assertGreaterEqual(thumb_file.stat().st_mtime, source_path.stat().st_mtime)

    def test_png_logo_preserves_transparency(self):
        logo_dir = self.images_dir / "tt1870479" / "logos"
        logo_dir.mkdir(parents=True)
        source_path = logo_dir / "1.png"

        # Create transparent RGBA image
        img = Image.new("RGBA", (1000, 400), color=(255, 255, 255, 0))
        img.save(source_path, "PNG")
        local_path = "images/tt1870479/logos/1.png"

        ensure_thumbnail(local_path, static_root=self.static_root)
        thumb_file = self.static_root / "thumbnails" / local_path
        self.assertTrue(thumb_file.is_file())

        with Image.open(thumb_file) as thumb_img:
            self.assertEqual(thumb_img.format, "PNG")
            self.assertIn(thumb_img.mode, ("RGBA", "P", "LA"))
            self.assertLessEqual(thumb_img.size[0], LOGO_CARD_SIZE[0])
            self.assertLessEqual(thumb_img.size[1], LOGO_CARD_SIZE[1])

    def test_small_jpeg_is_not_upscaled(self):
        source_path = self.images_dir / "small-still.jpg"
        target_path = self.static_root / "thumbnails" / "small-still.jpg"
        Image.new("RGB", (100, 50), color=(20, 30, 40)).save(source_path, "JPEG")

        self.assertTrue(
            generate_thumbnail(source_path, target_path, STILL_SIZE)
        )
        with Image.open(target_path) as thumbnail:
            self.assertEqual(thumbnail.size, (100, 50))

    def test_cache_paths_cannot_escape_static_root(self):
        escaped = "images/../../outside-still.jpg"
        self.assertEqual(
            ensure_thumbnail(escaped, static_root=self.static_root),
            escaped,
        )
        self.assertEqual(
            get_card_image_path(escaped, static_root=self.static_root),
            escaped,
        )

        windows_drive = "images/C:/outside-still.jpg"
        self.assertEqual(
            ensure_thumbnail(windows_drive, static_root=self.static_root),
            windows_drive,
        )


if __name__ == "__main__":
    unittest.main()


class ShrinkOriginalTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _image(self, name, size, mode="RGB", color=(40, 90, 160)):
        path = self.root / name
        Image.new(mode, size, color).save(path)
        return path

    def test_oversized_still_is_capped_and_keeps_ratio(self):
        path = self._image("tt2-still.jpg", (3840, 2160))
        self.assertTrue(shrink_original(path, "images/tt1/tt2-still.jpg"))
        with Image.open(path) as image:
            self.assertEqual((image.size, image.format), ((1920, 1080), "JPEG"))
        self.assertEqual([item.name for item in self.root.iterdir()], ["tt2-still.jpg"])

    def test_logo_keeps_transparency(self):
        path = self._image("1.png", (4000, 1000), "RGBA", (255, 255, 255, 0))
        self.assertTrue(shrink_original(path, "images/tt1/logos/1.png"))
        with Image.open(path) as image:
            self.assertEqual((image.size, image.format, image.mode), ((1000, 250), "PNG", "RGBA"))
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_images_within_the_limit_and_other_kinds_are_untouched(self):
        still = self._image("tt3-still.jpg", (1920, 1080))
        poster = self._image("poster.jpg", (4000, 6000))
        before = (still.read_bytes(), poster.read_bytes())
        self.assertFalse(shrink_original(still, "images/tt1/tt3-still.jpg"))
        self.assertFalse(shrink_original(poster, "images/tt1/posters/1.jpg"))
        self.assertEqual((still.read_bytes(), poster.read_bytes()), before)

    def test_unreadable_file_is_left_in_place(self):
        path = self.root / "broken.jpg"
        path.write_bytes(b"not an image")
        self.assertFalse(shrink_original(path, "images/tt1/backdrops/1.jpg"))
        self.assertEqual(path.read_bytes(), b"not an image")
