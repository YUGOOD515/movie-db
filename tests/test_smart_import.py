"""规则清洗、AI 接口、识别打分、扫描存储与失效检查。"""

import json
import os
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import test_library_app as fixtures
from connectors import alist, llm, throttle, tmdb
from models import discovery_store as store
from services import media_association, media_discovery as discovery, media_health, recognition
from services.name_cleaning import english_title, folder_hints, guess_position, normalize

ROOT = Path(__file__).resolve().parents[1]


def record(name, files, path=None, kind="folder"):
    base = path or f"/share/{name}"
    return {"path": base, "name": name, "kind": kind, "suggestion": None, "sort_time": 0,
            "files": [{"path": f"{base}/{item}", "name": item, "sort_time": 0} for item in files]}


class NameCleaningTest(unittest.TestCase):
    def test_disguised_folder_names(self):
        cases = {
            "鱿.鱼.游.戏": "鱿鱼游戏",
            "挽z救z计z划 (2026) 4K HDR & Dv": "挽救计划",
            "B 百丨年孤丨独 (2024)[哥伦比亚 历史 奇幻]": "百年孤独",
            "X《小谢尔顿》（2017-2024）1-7季全 1080P 中字": "小谢尔顿",
            "T 同乐者4K英语中英字幕2025": "同乐者",
            "【厚.实 (2026)】4K.HDR&杜比视界 内封精修简英双语": "厚实",
            "J 降临 4K原盘REMUX": "降临",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(folder_hints(name)["queries"][0], expected)
        hints = folder_hints("挽z救z计z划 (2026) 4K HDR & Dv")
        self.assertEqual(hints["year"], 2026)
        self.assertEqual(folder_hints("末日地堡 第二季")["season"], 2)
        self.assertEqual(folder_hints("White.Snake.2019.4K.60FPS.H265.10bit.WEB")["queries"][0], "White Snake")

    def test_positions_and_english_titles(self):
        self.assertEqual(guess_position("/s/Show.S02E05.1080p.mkv"), (2, 5))
        self.assertEqual(guess_position("/s/第3季/第12集.mp4"), (3, 12))
        self.assertEqual(guess_position("/s/Season 2/01.mp4"), (2, 1))
        self.assertEqual(guess_position("/s/EP07.mkv", season_hint=4), (4, 7))
        self.assertEqual(english_title(["/s/Squid.Game.S01E01.2160p.mkv", "/s/Squid.Game.S01E02.2160p.mkv"]), "Squid Game")

    def test_golden_set_from_real_shares(self):
        """media_names.json 来自真实网盘目录：规则清洗应覆盖大多数，季集推测接近全对。"""
        items = json.loads((ROOT / "media_names.json").read_text(encoding="utf-8"))
        groups = OrderedDict()
        for item in items:
            groups.setdefault(item["path"].split("/")[3], []).append(item)
        hits = total = 0
        for name, entries in groups.items():
            if entries[0]["source"] != "library":
                continue
            total += 1
            names = {normalize(entries[0]["display_title"] or ""), normalize(entries[0]["title"] or "")}
            queries = folder_hints(name)["queries"] + [english_title([entry["path"] for entry in entries]) or ""]
            hits += any(normalize(query) in names for query in queries if query)
        self.assertGreaterEqual(hits / total, 0.75)
        good = bad = 0
        for item in items:
            if item["source"] != "library" or item["type"] != "episode":
                continue
            season, episode = guess_position(item["path"], season_hint=folder_hints(item["path"].split("/")[3])["season"])
            good += (season or 1, episode) == (item["season"], item["episode"])
            bad += (season or 1, episode) != (item["season"], item["episode"])
        self.assertGreaterEqual(good / (good + bad), 0.95)


class LlmTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"LLM_API_KEY": "key", "LLM_MODEL": "model", "LLM_BASE_URL": "https://ai.test/v1/"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def response(self, status, payload):
        result = Mock(status_code=status)
        result.json.return_value = payload
        result.raise_for_status = Mock() if status < 400 else Mock(side_effect=requests.HTTPError(str(status)))
        return result

    def test_chat_completions_with_json_mode_fallback(self):
        answer = {"choices": [{"message": {"content": '```json\n{"kind": "tv", "candidates": []}\n```'}}]}
        with patch.object(llm.requests, "post", side_effect=[self.response(400, {}), self.response(200, answer)]) as post:
            self.assertEqual(llm.complete_json("system", "user"), {"kind": "tv", "candidates": []})
        first, second = post.call_args_list
        self.assertEqual(first.args[0], "https://ai.test/v1/chat/completions")
        self.assertIn("response_format", first.kwargs["json"])
        self.assertNotIn("response_format", second.kwargs["json"])
        self.assertEqual(first.kwargs["headers"]["Authorization"], "Bearer key")

    def test_responses_api(self):
        payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '说明 {"kind": "movie"} 结束'}]}]}
        with patch.dict(os.environ, {"LLM_API": "responses"}), \
             patch.object(llm.requests, "post", return_value=self.response(200, payload)) as post:
            self.assertEqual(llm.complete_json("system", "user"), {"kind": "movie"})
        body = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.args[0], "https://ai.test/v1/responses")
        self.assertEqual((body["instructions"], body["input"]), ("system", "user"))
        self.assertEqual(llm.describe()["api"], "chat")

    def test_deepseek_defaults_to_responses_without_thinking(self):
        payload = {"status": "completed", "output_text": '{"items": []}',
                   "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 800}, "output_tokens": 50}}
        usage = {}
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://api.deepseek.com/v1", "LLM_MODEL": "deepseek-flash"}), \
             patch.object(llm.requests, "post", return_value=self.response(200, payload)) as post:
            self.assertEqual(llm.complete_json("system", "user", max_tokens=500, usage=usage), {"items": []})
            self.assertEqual(llm.describe()["api"], "responses")
        self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/responses")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["reasoning"], {"effort": "none"})
        self.assertEqual(body["text"], {"format": {"type": "json_object"}})
        self.assertEqual((body["instructions"], body["input"], body["max_output_tokens"]), ("system", "user", 500))
        self.assertEqual(usage, {"requests": 1, "input_tokens": 1000, "cached_tokens": 800, "output_tokens": 50})

    def test_deepseek_chat_disables_thinking_and_classifies_errors(self):
        chat = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 4, "completion_tokens": 3}}
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://api.deepseek.com", "LLM_API": "chat"}):
            with patch.object(llm.requests, "post", return_value=self.response(200, chat)) as post:
                llm.complete_json("s", "u")
            body = post.call_args.kwargs["json"]
            self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/chat/completions")
            self.assertEqual((body["thinking"], body["response_format"]), ({"type": "disabled"}, {"type": "json_object"}))
            for status, fatal in ((401, True), (402, True), (422, False), (500, False)):
                with patch.object(llm.requests, "post", return_value=self.response(status, {"error": {"message": "x"}})), \
                     self.assertRaises(llm.LlmError) as caught:
                    llm.complete_json("s", "u")
                self.assertEqual((caught.exception.status, caught.exception.fatal), (status, fatal))
            truncated = {"choices": [{"message": {"content": '{"it'}, "finish_reason": "length"}]}
            with patch.object(llm.requests, "post", return_value=self.response(200, truncated)), \
                 self.assertRaises(llm.LlmError) as caught:
                llm.complete_json("s", "u")
            self.assertTrue(caught.exception.retry)
            empty = {"choices": [{"message": {"content": ""}}]}
            with patch.object(llm.requests, "post", side_effect=[self.response(200, empty), self.response(200, chat)]) as post:
                self.assertEqual(llm.complete_json("s", "u"), {"ok": True})  # JSON 模式偶发空内容，重试一次
            self.assertEqual(post.call_count, 2)

    def test_deepseek_is_limited_by_concurrency_not_rpm(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://api.deepseek.com", "LLM_RPM": "10"}):
            policy = throttle._llm_policy()
            self.assertEqual((policy.concurrency, policy.rate), (4, 20))
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://api.siliconflow.cn/v1", "LLM_RPM": "30"}):
            policy = throttle._llm_policy()
            self.assertEqual((policy.concurrency, policy.rate), (2, 0.5))

    def test_unconfigured(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}):
            self.assertFalse(llm.is_configured())
            with self.assertRaises(RuntimeError):
                llm.complete_json("s", "u")
        with self.assertRaises(ValueError):
            llm.parse_json("no json here")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "library.db"

    def test_roundtrip(self):
        self.assertEqual(store.store_path(self.database).name, "discovery.db")
        store.add_root(self.database, "/a"); store.add_root(self.database, "/b"); store.add_root(self.database, "/a")
        store.remove_root(self.database, "/b")
        self.assertEqual(store.list_roots(self.database), ["/a"])
        store.set_ignored(self.database, "/a/x", "x", True)
        self.assertEqual(store.ignored_paths(self.database), {"/a/x": "x"})
        store.set_ignored(self.database, "/a/x", "x", False)
        self.assertEqual(store.ignored_paths(self.database), {})
        store.save_alias(self.database, "abc", {"title_id": "tt1"})
        self.assertEqual(store.find_alias(self.database, "abc"), {"title_id": "tt1"})
        store.save_health(self.database, [("/a/1.mkv", "tt1", "missing"), ("/a/2.mkv", "tt1", "ok")])
        self.assertEqual([row["alist_path"] for row in store.missing_media(self.database)], ["/a/1.mkv"])
        store.save_snapshot(self.database, {"records": [1]})
        self.assertEqual(store.load_snapshot(self.database)["records"], [1])
        self.assertFalse(self.database.exists())


def search_results(**catalog):
    def search(query, media_type, page=1):
        results = []
        for tmdb_id, (kind, name, original, year) in catalog.items():
            if kind == media_type and (normalize(query) in normalize(name) or normalize(query) in normalize(original)):
                key, date = ("title", "release_date") if kind == "movie" else ("name", "first_air_date")
                results.append({"id": int(tmdb_id[1:]), key: name, f"original_{key}": original,
                                date: f"{year}-01-01", "popularity": 10})
        return {"results": results}
    return search


class RecognitionTest(unittest.TestCase):
    setUp = fixtures.LibraryIntegrationTest.setUp
    tearDown = fixtures.LibraryIntegrationTest.tearDown

    def patch_tmdb(self, **catalog):
        for name, fake in (
            ("search_media", search_results(**catalog)),
            ("fetch_title_summary", lambda media_type, tmdb_id: {
                "title_id": f"tt{tmdb_id}", "display_title": catalog[f"m{tmdb_id}"][1],
                "start_year": catalog[f"m{tmdb_id}"][3], "title_type": "movie"}),
            ("remember_media_identity", lambda *args: None),
        ):
            patcher = patch.object(tmdb, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_rule_match_is_ready_and_ambiguity_needs_review(self):
        self.patch_tmdb(m101=("movie", "挽救计划", "Project Hail Mary", 2026))
        result = recognition.recognize(self.database_path, record("挽z救z计z划 (2026) 4K", ["a.mkv"]), use_ai=False)
        self.assertEqual((result["status"], result["method"], result["title"]["title_id"]), ("ready", "rule", "tt101"))
        self.patch_tmdb(m201=("movie", "降临", "Arrival", 2016), m202=("movie", "降临", "The Coming", 2016))
        result = recognition.recognize(self.database_path, record("J 降临 4K原盘REMUX", ["a.mkv"]), use_ai=False)
        self.assertEqual(result["status"], "review")
        self.assertEqual(len(result["alternatives"]), 1)

    def test_library_alias_and_episode_mapping(self):
        episodes = {"episodes": [{"season_number": 1, "episode_number": number} for number in (1, 2, 3)]}
        with patch.object(media_association, "title_metadata", return_value=episodes):
            result = recognition.recognize(self.database_path, record("X《小谢尔顿》1080P", ["S01E01.mkv", "S01E02.mkv"]), use_ai=False)
        self.assertEqual((result["status"], result["coverage"], result["episodes"]), ("ready", 1, 2))
        self.assertEqual((result["method"], result["title"]["title_id"]), ("library", "tt6226232"))
        self.assertEqual(result["positions"]["/share/X《小谢尔顿》1080P/S01E02.mkv"], [1, 2])
        store.save_alias(self.database_path, recognition.alias_key("R任渔"), {"title_id": "tt0816692", "name": "星际穿越", "year": 2014, "media_type": "movie"})
        result = recognition.recognize(self.database_path, record("R任渔", ["a.mkv"]), use_ai=False)
        self.assertEqual((result["status"], result["method"], result["title"]["tmdb_id"]), ("ready", "learned", 157336))

    def test_ai_fallback_is_verified_against_tmdb(self):
        self.patch_tmdb(m301=("movie", "后室", "Backrooms", 2026))
        answer = {"kind": "movie", "candidates": [{"title": "后室", "original_title": "Backrooms", "year": 2026}]}
        with patch.object(llm, "is_configured", return_value=True), \
             patch.object(llm, "complete_json", return_value=answer) as ask:
            result = recognition.recognize(self.database_path, record("【厚.实 (2026)】4K", ["a.mkv"]))
        self.assertTrue(ask.called)
        self.assertEqual((result["method"], result["title"]["name"], result["confidence"]), ("ai", "后室", 0.9))
        with patch.object(llm, "is_configured", return_value=True), \
             patch.object(llm, "complete_json", return_value={"kind": "other", "candidates": []}):
            result = recognition.recognize(self.database_path, record("Adobe各版本大合集", ["教程.mp4"]))
        self.assertEqual((result["status"], result["not_media"]), ("unknown", True))


class BatchRecognitionTest(unittest.TestCase):
    tearDown = fixtures.LibraryIntegrationTest.tearDown
    patch_tmdb = RecognitionTest.patch_tmdb

    ANSWERS = {
        "【厚.实 (2026)】4K": {"kind": "movie", "candidates": [{"title": "后室", "original_title": "Backrooms", "year": 2026}]},
        "R任渔 2014": {"kind": "movie", "candidates": [{"title": "星际穿越", "original_title": "Interstellar", "year": 2014}]},
        "Q 某某某": {"kind": "movie", "candidates": [{"title": "人生切割术", "original_title": "Severance", "year": 2022}]},
    }

    def setUp(self):
        fixtures.LibraryIntegrationTest.setUp(self)
        self.patch_tmdb(m301=("movie", "后室", "Backrooms", 2026), m302=("movie", "星际穿越", "Interstellar", 2014),
                        m303=("movie", "人生切割术", "Severance", 2022))
        for patcher in (patch.object(llm, "is_configured", return_value=True),
                        patch.object(llm, "concurrency", return_value=1)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.records = [record(name, ["a.mkv"]) for name in self.ANSWERS]

    def run_batch(self, ask, size="20"):
        results = {}
        with patch.dict(os.environ, {"LLM_BATCH_SIZE": size}), patch.object(llm, "complete_json", side_effect=ask) as call:
            stats = recognition.recognize_many(self.database_path, self.records, titles=[],
                                               on_result=lambda item, result: results.update({item["name"]: result}))
        return stats, results, call

    def test_merges_records_into_one_request_and_retries_missing(self):
        def ask(system, user, **kwargs):
            items = json.loads(user)["items"]
            answered = [{"id": item["id"], **self.ANSWERS[item["name"]]} for item in items
                        if len(items) == 1 or item["name"] != "Q 某某某"]  # 整批时模型漏掉一项
            return {"items": answered}

        stats, results, call = self.run_batch(ask)
        self.assertEqual(call.call_count, 2)
        first, second = call.call_args_list
        self.assertEqual(first.args[0], recognition.SYSTEM_PROMPT)       # 固定前缀，利于上下文缓存
        self.assertEqual(second.args[0], recognition.SYSTEM_PROMPT)
        self.assertEqual(len(json.loads(first.args[1])["items"]), 3)
        self.assertEqual([item["name"] for item in json.loads(second.args[1])["items"]], ["Q 某某某"])
        self.assertEqual(first.kwargs["max_tokens"], 200 + 3 * recognition.OUTPUT_TOKENS_PER_ITEM)
        self.assertEqual({name: result["title"]["name"] for name, result in results.items()},
                         {"【厚.实 (2026)】4K": "后室", "R任渔 2014": "星际穿越", "Q 某某某": "人生切割术"})
        self.assertTrue(all(result["method"] == "ai" and recognition.ai_used(result) for result in results.values()))
        self.assertEqual((stats["needs_ai"], stats["requests"], stats["ai"], stats["failed"]), (3, 2, 3, 0))

    def test_truncated_batch_is_split(self):
        def ask(system, user, **kwargs):
            items = json.loads(user)["items"]
            if len(items) > 1:
                raise llm.LlmError("AI 输出被截断（达到输出 token 上限）。", retry=True)
            return {"items": [{"id": "1", **self.ANSWERS[items[0]["name"]]}]}

        stats, results, call = self.run_batch(ask)
        self.assertEqual([len(json.loads(item.args[1])["items"]) for item in call.call_args_list], [3, 1, 2, 1, 1])
        self.assertEqual(stats["ai"], 3)

    def test_fatal_error_stops_requests_and_is_not_cached_as_ai(self):
        stats, results, call = self.run_batch(
            Mock(side_effect=llm.LlmError("AI 账号余额不足，请充值后重新识别。", status=402, fatal=True)), size="1")
        self.assertEqual(call.call_count, 1)
        self.assertEqual((stats["failed"], len(results)), (3, 3))
        self.assertIn("余额不足", stats["error"])
        for item in self.records:
            result = results[item["name"]]
            self.assertIn("余额不足", result["ai_error"])
            self.assertFalse(recognition.ai_used(result))
            self.assertEqual(recognition.signature(item, ai=recognition.ai_used(result)), recognition.signature(item, ai=False))

    def test_rate_limit_defers_remaining_batches(self):
        stats, results, call = self.run_batch(Mock(side_effect=throttle.RateLimitError("llm", 3600)), size="2")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(stats["deferred"], 3)
        self.assertTrue(all(result.get("ai_deferred") for result in results.values()))


class DiscoveryStateTest(unittest.TestCase):
    setUp = fixtures.LibraryIntegrationTest.setUp
    tearDown = fixtures.LibraryIntegrationTest.tearDown

    def test_split_collection_and_scan_health(self):
        collection = record("【流.浪.地.球】全2部", ["The.Wandering.Earth.2019.mkv", "The.Wandering.Earth.II.2023.mkv"])
        self.assertEqual([item["kind"] for item in discovery.split_collection(collection)], ["file", "file"])
        series = record("Show", ["01.mkv", "02.mkv"])
        self.assertEqual(len(discovery.split_collection(series)), 1)
        associated = {"/r/A/1.mkv": {"title_id": "tt1"}, "/r/A/2.mkv": {"title_id": "tt1"},
                      "/r/Gone/1.mkv": {"title_id": "tt2"}, "/r/Broken/1.mkv": {"title_id": "tt3"}, "/x/1.mkv": {"title_id": "tt4"}}
        listings = {"/r": {"entries": {"A", "Broken"}, "complete": {"A"}, "seen": {"/r/A/1.mkv"}}}
        self.assertEqual(sorted(discovery._health_from_scan(associated, ["/r"], listings)), [
            ("/r/A/1.mkv", "tt1", "ok"), ("/r/A/2.mkv", "tt1", "missing"), ("/r/Gone/1.mkv", "tt2", "missing")])

    def test_full_scan_with_roots_ignore_and_restore(self):
        tree = {"/r": [{"name": "Kept", "is_dir": True}, {"name": "Skip", "is_dir": True}],
                "/r/Kept": [{"name": "S01E01.mkv"}, {"name": "S01E02.mkv"}], "/r/Skip": [{"name": "a.mkv"}]}
        store.add_root(self.database_path, "/r")
        store.set_ignored(self.database_path, "/r/Skip", "Skip", True)
        with patch.object(alist, "configured_token", return_value="t"), \
             patch.object(alist, "list_directory", side_effect=lambda path, token, **kw: tree[path]), \
             patch.object(media_health, "check_outside", return_value=[]), \
             patch.object(recognition, "prepare", side_effect=lambda db, item, **kw: {"record": item, "needs_ai": False}) as recognize, \
             patch.object(recognition, "finish", return_value={"status": "review", "title": None}):
            discovery._scans[str(self.database_path)] = discovery._empty_state("running")
            discovery._run_scan(str(self.database_path), self.database_path, False)
        state = discovery.scan_status(self.database_path)
        self.assertEqual(state["status"], "complete")
        self.assertEqual([item["name"] for item in state["records"]], ["Kept"])
        self.assertEqual(state["records"][0]["recognition"]["status"], "review")
        self.assertEqual(state["ignored"][0]["path"], "/r/Skip")
        self.assertEqual(recognize.call_count, 1)
        discovery._scans.pop(str(self.database_path))
        restored = discovery.scan_status(self.database_path)
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["records"][0]["path"], "/r/Kept")
        discovery._scans.pop(str(self.database_path))

    def test_routes_and_overview_health_filter(self):
        client = self.client
        self.assertEqual(client.post("/manager/discovery/roots", json={"path": "/Media", "action": "add"}).json["roots"], ["/Media"])
        self.assertEqual(client.post("/manager/discovery/roots", json={"path": "/Media", "action": "remove"}).json["roots"], [])
        self.assertEqual(client.post("/manager/discovery/roots", json={"path": "../x"}).status_code, 400)
        with patch.object(alist, "configured_token", return_value="t"), \
             patch.object(alist, "list_directory", return_value=[{"name": "B", "is_dir": True}, {"name": "a.mkv"}, {"name": "A", "is_dir": True}]):
            data = client.get("/manager/discovery/browse?path=/Media").json
        self.assertEqual([item["path"] for item in data["folders"]], ["/Media/A", "/Media/B"])
        self.assertEqual(data["parent"], "/")
        self.assertEqual(client.post("/manager/discovery/recognize", json={"path": "/nope"}).status_code, 400)
        self.assertTrue(client.post("/manager/discovery/ignore", json={"path": "/Media/A", "name": "A"}).json["ok"])
        store.save_health(self.database_path, [("/Movies/Interstellar.mkv", "tt0816692", "missing")])
        page = client.get("/manager?health=missing").get_data(as_text=True)
        self.assertIn("失效 1", page)
        self.assertIn("星际穿越", page)
        self.assertNotIn("爱乐之城", page)

    def test_outside_health_check_marks_only_confirmed_missing(self):
        def listing(path, token, **kw):
            if path == "/TV/Young Sheldon":
                return [{"name": "S01E01.mkv"}]
            raise RuntimeError("object not found" if path == "/Movies" else "timeout")
        with patch.object(alist, "list_directory", side_effect=listing):
            rows = media_health.check_outside(self.database_path, "t", [])
        status = {path: value for path, _, value in rows}
        self.assertEqual(status["/Movies/Interstellar.mkv"], "missing")
        self.assertEqual(status["/TV/Young Sheldon/S01E01.mkv"], "ok")
        self.assertEqual(status["/TV/Young Sheldon/S01E02.mkv"], "missing")
        missing = sum(1 for path, title_id, value in rows if title_id == "tt6226232" and value == "missing")
        self.assertGreaterEqual(missing, 1)
        self.assertEqual(media_health.missing_by_title(self.database_path).get("tt6226232"), missing)

    def test_autoscan_respects_switch(self):
        with patch.dict(os.environ, {"MOVIEDB_AUTOSCAN": "0", "ALIST_USERNAME": "u", "ALIST_PASSWORD": "p"}):
            self.assertFalse(discovery.start_autoscan(self.database_path))
        with patch.dict(os.environ, {"ALIST_USERNAME": "", "ALIST_PASSWORD": ""}):
            self.assertFalse(discovery.start_autoscan(self.database_path))

    def test_confirm_learns_alias(self):
        media_association._learn_alias(self.database_path, "R 人苼苆剨朮", {"title_id": "tt1", "name": "人生切割术", "year": 2022, "media_type": "tv"})
        self.assertEqual(store.find_alias(self.database_path, recognition.alias_key("R 人苼苆剨朮"))["name"], "人生切割术")


if __name__ == "__main__":
    unittest.main()
