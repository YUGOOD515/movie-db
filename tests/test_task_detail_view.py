"""后台任务详情（viewmodels/tasks.py 的 import_detail）：逐集小格、正在处理、部分失败的摘要。"""
import unittest

from viewmodels.tasks import import_detail


def row(state, items, probes=None, errors=None, link=0):
    return {"state": state, "payload": {"items": items}, "probes": probes or {},
            "result": {"rows": [{"path": path, "error": error} for path, error in (errors or {}).items()]} if errors else None,
            "progress": {"link": link}}


def summary(state, files, core, current=()):
    return {"id": "import:tt1:1", "name": "示例", "state": state, "files": files, "core": core,
            "current": list(current), "stages": [], "issues": []}


def episode(number, season=1):
    return {"path": f"/s/S{season:02}E{number:02}.mkv", "season_number": season, "episode_number": number}


class ImportDetailViewTest(unittest.TestCase):
    def test_running_series_marks_active_episode_and_seasons(self):
        items = [episode(1), episode(2), episode(1, season=2)]
        probes = {items[0]["path"]: {"video_width": 1920, "video_height": 1080}}
        detail = import_detail(summary("running", 3, 0, current=["S01E02"]), [row("running", items, probes, link=3)],
                               {"title_type": "series", "start_year": 2022, "display_title": "示例"})
        self.assertEqual(detail["meta"], "剧集 · 2022 · 3 个视频")
        self.assertTrue(detail["series"])
        self.assertEqual([file["season"] for file in detail["files"]], [1, 1, 2])
        self.assertEqual([file["state"] for file in detail["files"]], ["pending", "active", "pending"])
        self.assertEqual(detail["files"][0]["detail"], "1920×1080 · 等待写入")
        self.assertEqual(detail["current"], "S01E02 · S01E02.mkv")
        self.assertEqual(detail["outcome"], "")

    def test_partial_failure_counts_written_and_failed_episodes(self):
        written = [episode(number) for number in range(1, 4)]
        broken = [episode(4), episode(5)]
        detail = import_detail(summary("partial", 5, 3),
                               [row("complete", written), row("failed", broken, errors={broken[0]["path"]: "链接已失效"})],
                               None)
        self.assertEqual(detail["outcome"], "已关联 3 集，失败 1 集，未写入 1 集")
        self.assertEqual([file["state"] for file in detail["files"]][-2:], ["failed", "skipped"])
        self.assertEqual(detail["files"][3]["detail"], "链接已失效")

    def test_single_movie_uses_video_unit_without_seasons(self):
        item = {"path": "/m/Movie.2020.mkv"}
        detail = import_detail(summary("failed", 1, 0), [row("failed", [item], errors={item["path"]: "读取失败"})], None)
        self.assertFalse(detail["series"])
        self.assertIsNone(detail["files"][0]["season"])
        self.assertEqual(detail["outcome"], "失败 1 个视频")

    def test_retry_scope_waits_without_hiding_other_failures(self):
        items = [episode(1), episode(2), episode(3)]
        errors = {item["path"]: "上次读取失败" for item in items}
        for state in ("queued", "running"):
            with self.subTest(state=state):
                retry = row(state, items, errors=errors)
                retry["payload"]["retry_indexes"] = [1, 2]
                detail = import_detail(summary(state, 3, 0, current=["S01E02"] if state == "running" else []), [retry], None)
                self.assertEqual([file["state"] for file in detail["files"]],
                                 ["pending", "active" if state == "running" else "pending", "failed"])
                self.assertEqual(detail["files"][0]["detail"], "等待重试")
                self.assertEqual(detail["files"][2]["detail"], "上次读取失败")
        retry["payload"].pop("retry_indexes")
        detail = import_detail(summary("running", 3, 0), [retry], None)
        self.assertTrue(all(file["state"] == "pending" and file["detail"] == "等待重试" for file in detail["files"]))


if __name__ == "__main__":
    unittest.main()
