import json
import os
import unittest
from contextlib import closing
from unittest.mock import patch

import requests
import test_library_app as fixtures
from connectors import alist, media_probe, tmdb
from services import media_discovery as discovery, media_import


def video(path, created="2026-09-20T10:00:00+08:00"):
    return discovery._file(path, {"name": path.rsplit("/", 1)[1], "created": created})


class DiscoveryTest(unittest.TestCase):
    def test_new_folder_and_partially_linked_folder(self):
        folder = "/share/Series"
        files = [video(folder + "/S01E01.mkv"), video(folder + "/S01E02.mkv")]
        entry = {"name": "Series", "is_dir": True}
        new = discovery.group_pending(folder, entry, files, {})
        self.assertEqual((len(new), new[0]["kind"], len(new[0]["files"])), (1, "folder", 2))
        known = {files[0]["path"]: {"title_id": "tt123", "name": "剧集", "year": 2026, "title_type": "series", "season_number": 1}}
        update = discovery.group_pending(folder, entry, files, known)
        self.assertEqual([row["path"] for row in update], [files[1]["path"]])
        self.assertEqual(update[0]["suggestion"]["title_id"], "tt123")
        self.assertEqual(update[0]["suggestion"]["season_number"], 1)
        # Prefix collisions must not make a new folder look partially imported.
        self.assertEqual(discovery.group_pending(folder + "2", entry, [video(folder + "2/a.mkv")], known)[0]["kind"], "folder")

    def test_mixed_series_has_no_automatic_identity(self):
        known = {f"/share/Mixed/{number}.mkv": {"title_id": f"tt{number}", "name": "剧集", "year": 2026, "title_type": "series", "season_number": 1} for number in (1, 2)}
        rows = discovery.group_pending("/share/Mixed", {"is_dir": True}, [video("/share/Mixed/new.mkv")], known)
        self.assertIsNone(rows[0]["suggestion"])

    def test_failed_subtree_and_nonvideo_files_are_not_silent(self):
        def listing(path, *args, **kwargs):
            if path.endswith("broken"):
                raise requests.Timeout("private upstream")
            return [{"name": "broken", "is_dir": True}, {"name": "good.MKV"}, {"name": "subtitle.srt"}]
        with patch.object(alist, "list_directory", side_effect=listing):
            files, warnings = discovery.collect_folder("/share", "token")
        self.assertEqual([item["path"] for item in files], ["/share/good.MKV"])
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("private", warnings[0])

    def test_root_resolution_and_ambiguous_mounts(self):
        with patch.dict(os.environ, {"ALIST_IMPORT_ROOT": ""}), patch.object(alist, "list_directory") as listing:
            listing.side_effect = [[{"name": "Quark", "is_dir": True}], [{"name": "来自：分享", "is_dir": True}]]
            self.assertEqual(discovery.find_share_root("token"), "/Quark/来自：分享")
            listing.side_effect = [[{"name": "a", "is_dir": True}, {"name": "b", "is_dir": True}], [{"name": "来自：分享", "is_dir": True}], [{"name": "来自：分享", "is_dir": True}]]
            with self.assertRaisesRegex(ValueError, "多个"):
                discovery.find_share_root("token")

    def test_timestamp_fallback_and_invalid_paths(self):
        self.assertEqual(discovery._timestamp({"created": "0001-01-01T00:00:00Z", "modified": "2026-01-01T00:00:00Z"})[2], "modified")
        for path in ("relative", "/share/../secret.mkv", "/share//video.mkv", None):
            with self.assertRaises(ValueError):
                discovery.canonical_path(path)


class AssociationIntegrationTest(unittest.TestCase):
    setUp = fixtures.LibraryIntegrationTest.setUp
    tearDown = fixtures.LibraryIntegrationTest.tearDown
    connect = fixtures.LibraryIntegrationTest.connect

    def seed_discovery(self, paths):
        discovery._scans[str(self.database_path)] = {"status": "complete", "root": "/share", "records": [
            {"path": path, "sort_time": 0, "files": [video(path)]} for path in paths
        ], "warnings": [], "done": 1, "total": 1}
        self.addCleanup(lambda: discovery._scans.pop(str(self.database_path), None))

    def test_metadata_preflight_and_correction_are_read_only(self):
        self.seed_discovery(["/share/S01E01.mkv", "/share/S01E02.mkv"])
        before = self.database_path.read_bytes()
        title = self.client.get("/manager/titles/episodes?title_id=tt9000002").json
        self.assertEqual(len(title["episodes"]), 2)
        payload = {"title_id": "tt9000002", "items": [{"path": "/share/S01E01.mkv", "season_number": 2, "episode_number": 1}]}
        response = self.client.post("/manager/association/check", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertIn("没有 S02 E01", response.json["rows"][0]["error"])
        payload["items"][0]["season_number"] = 1
        response = self.client.post("/manager/association/check", json=payload)
        self.assertTrue(response.json["valid"])
        self.assertEqual(response.json["rows"][0]["target"]["title_id"], "tt9000003")
        self.assertEqual(before, self.database_path.read_bytes())

    def test_invalid_batch_and_duplicate_positions(self):
        paths = ["/share/one.mkv", "/share/two.mkv"]
        self.seed_discovery(paths)
        payload = {"title_id": "tt9000002", "items": [{"path": path, "season_number": 1, "episode_number": 1} for path in paths]}
        response = self.client.post("/manager/association/check", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertIn("同一季集", response.json["rows"][1]["error"])
        payload["items"][1]["path"] = "/outside/file.mkv"
        self.assertIn("发现结果", self.client.post("/manager/association/check", json=payload).json["rows"][1]["error"])
        for payload in (None, [], {"items": []}, {"title_id": "tt9000002", "items": [None]}, {"title_id": "tt9000002", "items": [{"path": "/share/../x"}]}):
            self.assertEqual(self.client.post("/manager/association/check", json=payload).status_code, 400)

    def test_movie_batch_series_single_update_and_stale_selection(self):
        paths = ["/share/Movie.mkv", "/share/S01E01.mkv", "/share/S01E02.mkv"]
        self.seed_discovery(paths)
        with patch.object(alist, "configured_token", return_value="test"), \
             patch.object(alist, "get_signed_url", side_effect=lambda path, token: alist.get_origin() + "/d" + path + "?sign=test"), \
             patch.object(alist, "validate_alist_video_url"), \
             patch.object(alist, "get_video_link", return_value="https://video.test/file.mkv"), \
             patch.object(media_probe, "probe_video", return_value={"duration_ms": 1000}), \
             patch.object(media_import, "fetch_title_rating", return_value={"status": "unavailable"}):
            movie = {"title_id": "tt9000001", "items": [{"path": paths[0]}]}
            self.assertEqual(self.client.post("/manager/association/complete", json=movie).json["imported"], 1)
            series = {"title_id": "tt9000002", "items": [{"path": path, "season_number": 1, "episode_number": index} for index, path in enumerate(paths[1:], 1)]}
            response = self.client.post("/manager/association/complete", json=series)
            self.assertEqual(response.json["imported"], 2)
            self.assertEqual(self.client.post("/manager/association/complete", json=series).status_code, 422)
            # A newly found alternative file for an existing episode follows the same single-update path.
            updated = "/share/new/S01E02.mkv"
            self.seed_discovery([updated])
            series["items"] = [{"path": updated, "season_number": 1, "episode_number": 2}]
            self.assertEqual(self.client.post("/manager/association/complete", json=series).json["imported"], 1)
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM media_file WHERE alist_path LIKE '/share/%'").fetchone()[0], 4)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_failed_import_rolls_back_and_keeps_row_feedback(self):
        paths = ["/share/S01E01.mkv", "/share/S01E02.mkv"]
        self.seed_discovery(paths)
        before = self.database_path.read_bytes()
        with patch.object(alist, "configured_token", return_value="test"), \
             patch.object(alist, "get_signed_url", side_effect=lambda path, token: alist.get_origin() + "/d" + path + "?sign=secret"), \
             patch.object(alist, "validate_alist_video_url"), \
             patch.object(alist, "get_video_link", return_value="https://video.test/file.mkv"), \
             patch.object(media_probe, "probe_video", side_effect=[{"duration_ms": 1000}, requests.Timeout("sign=secret")]), \
             patch.object(media_import, "fetch_title_rating", return_value={"status": "unavailable"}):
            payload = {"title_id": "tt9000002", "items": [{"path": path, "season_number": 1, "episode_number": index} for index, path in enumerate(paths, 1)]}
            response = self.client.post("/manager/association/complete", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.json["rows"][1]["error"])
        self.assertNotIn("secret", response.get_data(as_text=True))
        self.assertEqual(before, self.database_path.read_bytes())

    def test_streamed_import_reports_each_file(self):
        paths = ["/share/S01E01.mkv", "/share/S01E02.mkv"]
        self.seed_discovery(paths)
        with patch.object(alist, "configured_token", return_value="test"), \
             patch.object(alist, "get_signed_url", side_effect=lambda path, token: alist.get_origin() + "/d" + path + "?sign=secret"), \
             patch.object(alist, "validate_alist_video_url"), \
             patch.object(alist, "get_video_link", return_value="https://video.test/file.mkv"), \
             patch.object(media_probe, "probe_video", return_value={"duration_ms": 1000}), \
             patch.object(media_import, "fetch_title_rating", return_value={"status": "unavailable"}):
            payload = {"title_id": "tt9000002", "items": [{"path": path, "season_number": 1, "episode_number": index} for index, path in enumerate(paths, 1)]}
            response = self.client.post("/manager/association/complete?stream=1", json=payload)
            self.assertEqual(response.mimetype, "application/x-ndjson")
            lines = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]
            # 重复提交同一批文件：流里只有校验结果，没有进度。
            again = [json.loads(line) for line in self.client.post("/manager/association/complete?stream=1", json=payload).get_data(as_text=True).splitlines()]
        steps = [(line["progress"]["phase"], line["progress"]["done"]) for line in lines if "progress" in line]
        self.assertEqual(steps, [("link", 0), ("link", 1), ("link", 2), ("probe", 1), ("probe", 2), ("write", 0)])
        self.assertEqual(lines[-1]["result"]["imported"], 2)
        self.assertNotIn("secret", "".join(map(json.dumps, lines)))
        self.assertEqual(len(again), 1)
        self.assertFalse(again[0]["result"]["valid"])
        self.assertIn("error", self.client.post("/manager/association/complete?stream=1", json={"items": []}).get_data(as_text=True))
