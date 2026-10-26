import unittest
from unittest.mock import Mock, patch

from connectors import alist
from scripts.normalize_alist_names import normalize_media, scan_alist


class NormalizeAListNamesTest(unittest.TestCase):
    def test_library_metadata_overrides_weak_filename_guess(self):
        path = "/Quark/来自：分享/[小谢尔顿][全1-7季]/S01/S01.01.mkv"
        library_media = {
            path: {
                "file_id": 1,
                "title_id": "tt6226232",
                "title_type": "episode",
                "primary_title": "Pilot",
                "display_title": "试播集",
                "start_year": 2017,
                "series_id": "tt6226232-series",
                "season_number": 1,
                "episode_number": 1,
                "series_title": "Young Sheldon",
                "series_display_title": "小谢尔顿",
            }
        }

        record = normalize_media(path, library_media)

        self.assertEqual(record["source"], "library")
        self.assertEqual(record["title_id"], "tt6226232")
        self.assertEqual(record["standard_filename"], "Young Sheldon S01E01 - Pilot.mkv")

        unknown = normalize_media("/Clips/S01.01.mkv")
        self.assertEqual((unknown["source"], unknown["type"]), ("unknown", "unknown"))
        self.assertEqual(unknown["standard_filename"], "S01.01.mkv")

    def test_parenthesized_directories_do_not_replace_the_file_title(self):
        samples = {
            "/Quark/来自：分享/西部世界 (2016)/第 1 季 - 2160p UHD BluRay HDR DV HEVC TrueHD 7.1 Atmos/Westworld S01E01 - 第 1 集 - 2160p UHD BluRay HDR DV HEVC TrueHD 7.1 Atmos.mkv": "Westworld S01E01 - 第 1 集.mkv",
            "/Quark/来自：分享/暗黑 (全系列)/Dark.S01.2017.2160p.NF.WEB-DL.DDP5.1.Atmos.H.265-HHWEB/Dark.S01E01.2017.2160p.NF.WEB-DL.DDP5.1.Atmos.H.265-HHWEB.mkv": "Dark S01E01.mkv",
            "/Quark/来自：分享/群星（2024）4K HDR 杜比视界 高码率 外挂中英特效字幕/Constellation.S01E01.The.Wounded.Angel.2160p.ATVP.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-FLUX.mkv": "Constellation S01E01 - The Wounded Angel.mkv",
            "/Quark/来自：分享/盗梦空间 (2010)/Inception (2010) - 2160p UHD BluRay HDR HEVC DTS 1.mkv": "Inception (2010).mkv",
        }

        self.assertEqual(
            {path: normalize_media(path)["standard_filename"] for path in samples},
            samples,
        )

    def test_recursive_scan_normalizes_movies_and_episodes(self):
        tree = {
            "/": [
                {"name": "Movies", "is_dir": True},
                {"name": "TV", "is_dir": True},
                {"name": "readme.txt", "is_dir": False},
            ],
            "/Movies": [{"name": "Dune.Part.Two.2024.2160p.mkv", "is_dir": False}],
            "/TV": [{"name": "The Last of Us", "is_dir": True}],
            "/TV/The Last of Us": [{"name": "Season 01", "is_dir": True}],
            "/TV/The Last of Us/Season 01": [
                {"name": "S01E02.Infected.1080p.WEB-DL.mkv", "is_dir": False}
            ],
        }

        records = scan_alist(["/"], tree.__getitem__)

        self.assertEqual(
            [record["standard_filename"] for record in records],
            ["Dune Part 2 (2024).mkv", "The Last of Us S01E02 - Infected.mkv"],
        )

    def test_list_directory_uses_the_alist_api(self):
        response = Mock(status_code=200)
        response.json.return_value = {"code": 200, "data": {"content": [{"name": "Movies", "is_dir": True}]}}
        with patch.object(alist.requests, "post", return_value=response) as post:
            entries = alist.list_directory("/", "token")

        self.assertEqual(entries, [{"name": "Movies", "is_dir": True}])
        self.assertEqual(post.call_args.kwargs["json"]["per_page"], -1)
        response.raise_for_status.assert_called_once_with()

        response.json.return_value = {"code": 401, "message": "unauthorized", "data": None}
        with patch.object(alist.requests, "post", return_value=response), self.assertRaisesRegex(RuntimeError, "unauthorized"):
            alist.list_directory("/", "bad-token")


if __name__ == "__main__":
    unittest.main()
