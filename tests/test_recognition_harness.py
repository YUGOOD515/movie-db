"""目录结构、文件归属和语义纠错；网络与模型均打桩，不把模型自评当作正确答案。"""

from collections import Counter
import json
import sqlite3
from pathlib import PurePosixPath

import pytest

from connectors import llm
from services import media_association, media_discovery, media_grouping, recognition, recognition_harness as harness


def video(path):
    return {"path": path, "name": PurePosixPath(path).name, "size": 1, "modified": "v1", "sort_time": 0}


def special_files():
    """与实际 Special 相同的层级/数量及特殊命名，替换无关目录名。"""
    root = "/drive/video/Special"
    files = []
    for title, count in [("Blueming", 11), ("ShowA", 3), ("ShowB", 24), ("ShowC", 8),
                         ("ShowD", 4), ("ShowE", 8), ("ShowF", 10), ("语义错误", 8)]:
        files.extend(video(f"{root}/Category/{title}/{title}.S01E{n:02}.mp4") for n in range(1, count + 1))
    files.append(video(f"{root}/Category/语义错误/语义错误.S01语义错误 电影版.mkv"))
    files.append(video(f"{root}/Category/Movie2017/Movie.2017.mkv"))
    files.extend(video(f"{root}/Tiny.World.S01/Tiny.World.S01E{n:02}.mkv") for n in range(1, 7))
    files.extend(video(f"{root}/Category/SKAM/SKAM-Franch-S3/Skam.France.S3E{n:02}.mp4") for n in range(2, 10))
    files.append(video(f"{root}/Category/SKAM/SKAM-Franch-S3/SKAM.Franch.S03E01.mp4"))
    files.extend(video(f"{root}/Category/SKAM/SKAM-Franch-S3/第五集/{name}.mp4") for name in ("b", "c"))
    files.extend(video(f"{root}/Category/SKAM/SKAM-Norway-S03-1080p/Skam.Norway.S03E{n:02}.mkv") for n in range(10))
    return root, files


def test_nested_special_partitions_105_files_without_loss_or_duplication():
    root, files = special_files()
    groups = media_grouping.partition(root, files)
    assert len(files) == 105 and len(groups) == 13
    assert Counter(f["path"] for g in groups for f in g["files"]) == Counter(f["path"] for f in files)
    assert len(next(g for g in groups if g["name"] == "SKAM-Franch-S3")["files"]) == 11
    assert len(next(g for g in groups if g["name"] == "SKAM-Norway-S03-1080p")["files"]) == 10
    assert len([g for g in groups if "语义错误" in g["name"]]) == 2


def test_season_and_quality_folders_preserve_show_and_all_versions():
    files = [video(f"/r/Show/{season}/{quality}/Show.S0{n}E01.mkv")
             for n, season in [(1, "Season 1"), (2, "第二季")] for quality in ("1080P-HDR", "4K-SDR[Team]")]
    groups = media_grouping.partition("/r/Show", files)
    assert len(groups) == 1 and groups[0]["path"] == "/r/Show"
    assert len(groups[0]["files"]) == 4
    assert len(media_grouping.samples(groups[0])) == 4


def test_same_title_movies_with_different_years_are_split():
    files = [video("/r/电影/Dune.2021.2160p.WEB-DL.mkv"), video("/r/电影/Dune.1984.1080p.BluRay.mkv"),
             video("/r/电影/Dune.2021.1080p.mkv"), video("/r/电影/Dune.4K.mkv")]
    groups = media_grouping.partition("/r", files)
    names = sorted(sorted(f["name"] for f in g["files"]) for g in groups)
    # 无年份的文件并入第一组同名电影；相差一年以内的版本仍算同一部。
    assert names == [["Dune.1984.1080p.BluRay.mkv"], ["Dune.2021.1080p.mkv", "Dune.2021.2160p.WEB-DL.mkv", "Dune.4K.mkv"]] or \
        names == [["Dune.1984.1080p.BluRay.mkv", "Dune.4K.mkv"], ["Dune.2021.1080p.mkv", "Dune.2021.2160p.WEB-DL.mkv"]]
    near = media_grouping.partition("/r", [video("/r/M/Movie.2019.mkv"), video("/r/M/Movie.2020.REMUX.mkv")])
    assert len(near) == 1
    show = media_grouping.partition("/r", [video("/r/S/Show.2019.S01E01.mkv"), video("/r/S/Show.2021.S02E01.mkv")])
    assert len(show) == 1


def test_mixed_movie_and_series_get_independent_types():
    root, files = special_files()
    groups = media_grouping.partition(root, files)
    movie = next(g for g in groups if "电影版" in g["name"])
    series = next(g for g in groups if "语义错误" in g["name"] and "电影版" not in g["name"])
    assert recognition.guess_media_type(movie, {"season": 1}) == "movie"
    assert recognition.guess_media_type(series, {}) == "tv"


def test_partial_import_never_gives_new_sibling_another_series_identity():
    files = [video("/r/Category/Old/Old.S01E01.mkv"), video("/r/Category/New/New.S01E01.mkv")]
    known = {files[0]["path"]: {"title_id": "tt1", "title_type": "series", "name": "Old", "year": 2020, "season_number": 1}}
    groups = media_discovery.group_pending("/r/Category", {"is_dir": True}, files, known)
    assert len(groups) == 1 and groups[0]["name"] == "New" and groups[0]["suggestion"] is None


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "cache_identity", lambda: "test-model")


def test_planner_repairs_missing_members_and_persists_only_valid_plan(configured, monkeypatch, tmp_path):
    files = [video("/r/A/A.S01E01.mkv"), video("/r/B/B.S01E01.mkv")]
    drafts = media_grouping.partition("/r", files)
    calls = []
    def ask(system, user, **kwargs):
        packet = json.loads(user)
        calls.append(packet)
        rows = [{"name": unit["name"], "unit_ids": [unit["id"]], "review": False} for unit in packet["drafts"]]
        return {"groups": rows[:1] if len(calls) == 1 else rows}
    monkeypatch.setattr(llm, "complete_json", ask)
    result = harness.plan_groups(tmp_path / "library.db", "/r", drafts)
    assert len(calls) == 2 and "遗漏 1" in calls[1]["feedback"]
    assert Counter(f["path"] for g in result for f in g["files"]) == Counter(f["path"] for f in files)
    again = harness.plan_groups(tmp_path / "library.db", "/r", drafts)
    assert again == result and len(calls) == 2


def test_planner_rejects_merging_distinct_titles_and_preserves_fallback(configured, monkeypatch, tmp_path):
    drafts = media_grouping.partition("/r", [video("/r/Arrival/Arrival.2016.mkv"), video("/r/Fallout/Fallout.S01E01.mkv")])
    calls = []
    def ask(system, user, **kwargs):
        calls.append(json.loads(user))
        return {"groups": [{"name": "Some title", "unit_ids": ["u1", "u2"], "review": False}]}
    monkeypatch.setattr(llm, "complete_json", ask)
    result = harness.plan_groups(tmp_path / "library.db", "/r", drafts)
    assert len(calls) == harness.MAX_REQUESTS
    assert [g["files"] for g in result] == [g["files"] for g in drafts]
    assert result[0]["grouping_trace"]["exhausted"]
    assert "混合" in calls[-1]["feedback"]


def test_explicit_nonmedia_collection_keeps_all_files_without_title_search(configured, monkeypatch, tmp_path):
    drafts = media_grouping.partition("/r/Stock", [video("/r/Stock/Aerial.City.mp4"), video("/r/Stock/Train.Station.mp4")])
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: {"groups": [
        {"name": "城市航拍", "unit_ids": ["u1"], "kind": "other", "review": False},
        {"name": "车站素材", "unit_ids": ["u2"], "kind": "other", "review": False}]})
    groups = harness.plan_groups(tmp_path / "library.db", "/r/Stock", drafts)
    assert len(groups) == 1 and len(groups[0]["files"]) == 2
    monkeypatch.setattr(recognition, "_search", lambda *args: pytest.fail("nonmedia must not search titles"))
    context = recognition.prepare(tmp_path / "library.db", groups[0], titles=[])
    assert recognition.complete(tmp_path / "library.db", context)["not_media"]


def test_documentary_with_episode_markers_cannot_be_hidden_as_stock(configured, monkeypatch, tmp_path):
    drafts = media_grouping.partition("/r", [video("/r/TinyWorld/Tiny.World.S01E01.mkv"), video("/r/Other/01.mkv")])
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: {"groups": [
        {"name": "Stock", "unit_ids": ["u1", "u2"], "kind": "other", "review": False}]})
    groups = harness.plan_groups(tmp_path / "library.db", "/r", drafts)
    assert all(g.get("grouping_kind") != "other" for g in groups)
    assert sum(len(g["files"]) for g in groups) == 2


def test_degraded_ai_cache_stays_visible_while_next_scan_can_retry(configured, monkeypatch, tmp_path):
    record = media_grouping.partition("/r/Show", [video("/r/Show/Show.S01E01.mkv")])[0]
    database = tmp_path / "library.db"
    state = {**media_discovery._empty_state("complete"), "records": [record]}
    monkeypatch.setitem(media_discovery._scans, str(database), state)
    monkeypatch.setattr(media_discovery.store, "list_roots", lambda *args: [])
    monkeypatch.setattr(media_discovery.store, "load_recognitions", lambda *args: {record["path"]: {
        "signature": recognition.signature(record, ai=False), "status": "review", "ai_error": "暂不可用",
        "title": {"title_id": "tt1", "name": "Show"}}})
    monkeypatch.setattr(media_discovery, "connect_database", lambda *args: sqlite3.connect(":memory:"))
    monkeypatch.setattr(media_discovery, "get_associated_paths", lambda *args: {})
    from services import media_health
    monkeypatch.setattr(media_health, "missing_by_title", lambda *args: {})
    shown = media_discovery.scan_status(database)["records"][0]["recognition"]
    assert shown["title"]["title_id"] == "tt1" and shown["ai_error"]
    assert recognition.signature(record, ai=False) != recognition.signature(record)


@pytest.mark.parametrize("refs", [["u1", "u1", "u2"], ["u1", "outside"]])
def test_duplicate_and_unknown_unit_ids_never_replace_the_snapshot(configured, monkeypatch, tmp_path, refs):
    drafts = media_grouping.partition("/r", [video("/r/A/01.mkv"), video("/r/B/01.mkv")])
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: {"groups": [
        {"name": "Unknown", "unit_ids": refs, "review": True}]})
    groups = harness.plan_groups(tmp_path / "library.db", "/r", drafts)
    assert [g["files"] for g in groups] == [g["files"] for g in drafts]
    assert groups[0]["grouping_trace"]["exhausted"]


def test_planner_can_inspect_and_split_unit_into_file_ids(configured, monkeypatch, tmp_path):
    drafts = media_grouping.partition("/r", [video("/r/A/01.mp4"), video("/r/A/02.mp4"), video("/r/B/01.mp4")])
    calls = []
    def ask(system, user, **kwargs):
        packet = json.loads(user)
        calls.append(packet)
        if len(calls) == 1:
            return {"tools": [{"name": "list_files", "unit_id": "u1"}]}
        ids = [f["id"] for f in packet["observations"][0]["result"]["files"]]
        return {"groups": [{"name": "A part one", "file_ids": ids[:1], "review": True},
                           {"name": "A part two", "file_ids": ids[1:], "review": True},
                           {"name": "B", "unit_ids": ["u2"], "review": True}]}
    monkeypatch.setattr(llm, "complete_json", ask)
    groups = harness.plan_groups(tmp_path / "library.db", "/r", drafts)
    assert len(groups) == 3 and sum(len(g["files"]) for g in groups) == 3
    assert groups[0]["grouping_trace"]["tools"] == 1


def test_duplicate_unknown_members_and_tools_are_bounded(configured, monkeypatch, tmp_path):
    drafts = media_grouping.partition("/r", [video("/r/A/01.mkv"), video("/r/B/01.mkv")])
    calls = []
    def ask(*args, **kwargs):
        calls.append(1)
        return {"tools": [{"name": "write_file", "path": "outside"}]}
    monkeypatch.setattr(llm, "complete_json", ask)
    groups = harness.plan_groups(tmp_path / "library.db", "/r", drafts)
    assert len(calls) == 3 and len(groups) == 2
    assert groups[0]["grouping_trace"]["exhausted"]


def test_high_rule_score_with_low_episode_coverage_invokes_tool_repair(configured, monkeypatch):
    record = media_grouping.partition("/r/Show", [video("/r/Show/Show.S01E01.mkv"), video("/r/Show/Show.S01E02.mkv")])[0]
    title = {"title_id": "tt1", "tmdb_id": 1, "name": "Show", "original_name": "Show", "media_type": "tv", "year": 2024}
    context = {"record": record, "result": {"status": "unknown", "title": None}, "hints": {"season": 1, "year": None, "queries": ["Show"]},
               "english": "Show", "expected": "tv", "titles": [], "title": title, "confidence": 0.95,
               "method": "rule", "ranked": [], "harness_enabled": True}
    monkeypatch.setattr(media_association, "title_metadata", lambda db, tid: {"episodes": [
        {"season_number": 1, "episode_number": n} for n in range(1, 2 if tid == "tt1" else 3)]})
    candidate = {"tmdb_id": 2, "media_type": "tv", "name": "Show", "original_name": "Show", "year": 2024, "popularity": 10, "score": 0.9}
    monkeypatch.setattr(recognition, "_search_results", lambda *args: [candidate])
    monkeypatch.setattr(recognition, "_search", lambda *args: [candidate])
    monkeypatch.setattr(recognition, "_resolve", lambda candidate, db: {**title, "tmdb_id": candidate["tmdb_id"], "title_id": f"tt{candidate['tmdb_id']}"})
    calls = []
    def ask(system, user, **kwargs):
        packet = json.loads(user)
        calls.append(packet)
        if len(calls) == 1:
            return {"tools": [{"name": "search_tmdb", "query": "Show", "media_type": "tv"}]}
        return {"kind": "tv", "candidates": [{"title": "Show", "year": 2024}]}
    monkeypatch.setattr(llm, "complete_json", ask)
    result = recognition.complete(None, context)
    assert result["title"]["title_id"] == "tt2" and result["coverage"] == 1 and result["status"] == "ready"
    assert result["harness"]["requests"] == 2 and result["harness"]["tools"] == 1
    assert "季集" in calls[0]["feedback"][0]


def test_episode_numbers_alone_cannot_confirm_another_show(monkeypatch):
    record = media_grouping.partition("/r/Fallout", [video("/r/Fallout/Fallout.S01E01.mkv")])[0]
    monkeypatch.setattr(media_association, "title_metadata", lambda *args: {"episodes": [{"season_number": 1, "episode_number": 1}]})
    context = {"record": record, "result": {}, "hints": {"season": 1}, "title": {
        "title_id": "tt1", "tmdb_id": 1, "name": "Tiny World", "original_name": "Tiny World", "media_type": "tv"},
        "confidence": 0.95, "method": "rule", "ranked": []}
    result = recognition.finish(None, context)
    assert result["coverage"] == 1 and result["status"] == "review"
    assert "文件片名与候选作品不一致" in result["issues"]


def test_signature_changes_with_model_or_context(monkeypatch):
    record = media_grouping.partition("/r/Show", [video("/r/Show/01.mkv")])[0]
    monkeypatch.setenv("LLM_MODEL", "model-a")
    first = recognition.signature(record, ai=True)
    monkeypatch.setenv("LLM_MODEL", "model-b")
    assert recognition.signature(record, ai=True) != first
    assert recognition.signature({**record, "context_path": "/r/Other"}, ai=True) != recognition.signature(record, ai=True)


def test_missing_episode_imdb_ids_do_not_trigger_futile_ai_retries(configured, monkeypatch):
    record = media_grouping.partition("/r/Show", [video("/r/Show/Show.S01E01.mkv")])[0]
    monkeypatch.setattr(media_association, "title_metadata", lambda *args: {"episodes": [], "catalog_positions": [[1, 1]]})
    monkeypatch.setattr(harness, "repair", lambda *args, **kwargs: pytest.fail("missing external IDs cannot be repaired by AI"))
    context = {"record": record, "result": {}, "hints": {"season": 1}, "title": {
        "title_id": "tt1", "tmdb_id": 1, "name": "Show", "media_type": "tv"},
        "confidence": 0.95, "method": "rule", "ranked": [], "harness_enabled": True}
    result = recognition.complete(None, context)
    assert result["status"] == "review" and result["missing_episode_ids"] == 1 and result["unmatched_episodes"] == 0
    assert result["issues"] == ["TMDB 部分单集缺少 IMDb ID，暂不可关联"]


def test_hangul_original_title_is_not_a_conflicting_english_filename(monkeypatch):
    record = media_grouping.partition("/r/Blueming", [video("/r/Blueming/Blueming.S01E01.mkv")])[0]
    monkeypatch.setattr(media_association, "title_metadata", lambda *args: {"episodes": [], "catalog_positions": [[1, 1]]})
    result = recognition.finish(None, {"record": record, "result": {}, "hints": {"season": 1}, "title": {
        "title_id": "tt1", "tmdb_id": 1, "name": "为你绽放", "original_name": "블루밍", "media_type": "tv"},
        "confidence": 0.9, "method": "rule", "ranked": []})
    assert "文件片名与候选作品不一致" not in result["issues"]


@pytest.mark.parametrize("answer", [{"kind": "invalid", "candidates": []}, {"kind": "tv", "candidates": "bad"},
                                     {"kind": "movie", "candidates": [{"title": "A", "year": True}]},
                                     {"kind": "other", "candidates": [{"title": "A"}]}])
def test_invalid_structured_answers_are_rejected(answer):
    assert recognition._normalize_answer(answer) is None


def edgerunners_s02_files():
    """《赛博朋克：边缘行者》S02 的实际布局：同一季四个画质目录，中文名、英文名、只有单集标题三种命名。"""
    root = "/drive/来自：分享/B.【4K.完结.全10集】缘行.者.S02"
    files = []
    for n in range(1, 3):
        files.append(video(f"{root}/4K SDR 内封字幕/赛博朋克 边缘行者.S02E{n:02}.2026.2160p.WEB-DL.H265.SDR.24fps.10bit.DDP5.1.2Audios.mkv"))
        files.append(video(f"{root}/4K DV.HDR 内封字幕/Cyberpunk.Edgerunners.S02E{n:02}.2026.2160p.NF.WEB-DL.H265.DV.HDR.DDP5.1.2Audio.mkv"))
        files.append(video(f"{root}/4K HDR 内封字幕/S02E{n:02}.Episode.Title.{n}.2160p.NF.WEB-DL.DDP5.1.HDR.H.265.mkv"))
    return root, files


EDGERUNNERS = {"title_id": "tt12590266", "tmdb_id": 105248, "name": "赛博朋克：边缘行者",
               "original_name": "Cyberpunk: Edgerunners", "media_type": "tv", "year": 2022}


def test_split_quality_folders_marked_for_review_become_ready_once_episodes_verify(configured, monkeypatch, tmp_path):
    """AI 因命名不同把同一季的画质目录拆开并标 review；身份与季集全部核对通过后不应再卡在“目录分组仍需核对”。"""
    root, files = edgerunners_s02_files()
    drafts = media_grouping.partition(root, files)
    def plan(system, user, **kwargs):
        packet = json.loads(user)
        return {"groups": [{"name": unit["name"], "unit_ids": [unit["id"]], "review": True} for unit in packet["drafts"]]}
    monkeypatch.setattr(llm, "complete_json", plan)
    groups = harness.plan_groups(tmp_path / "library.db", root, drafts) if len(drafts) > 1 else [{**drafts[0], "grouping_review": True}]
    assert groups and all(group["grouping_review"] for group in groups)
    monkeypatch.setattr(media_association, "title_metadata", lambda *args: {"episodes": [
        {"season_number": 2, "episode_number": n, "title_id": f"tt9{n}"} for n in range(1, 11)]})
    monkeypatch.setattr(harness, "repair", lambda *args, **kwargs: pytest.fail("verified groups need no AI repair"))
    for group in groups:
        context = {"record": group, "result": {}, "hints": {"season": 2}, "title": EDGERUNNERS,
                   "confidence": 0.9, "method": "ai", "ranked": [], "harness_enabled": True}
        result = recognition.complete(None, context)
        assert result["coverage"] == 1 and "issues" not in result, group["name"]
        assert result["status"] == "ready"


def test_grouping_review_still_blocks_when_episodes_do_not_verify(monkeypatch):
    record = {**media_grouping.partition("/r/Show", [video("/r/Show/Show.S01E01.mkv"), video("/r/Show/Show.S01E09.mkv")])[0],
              "grouping_review": True}
    monkeypatch.setattr(media_association, "title_metadata", lambda *args: {"episodes": [
        {"season_number": 1, "episode_number": 1, "title_id": "tt91"}]})
    result = recognition.finish(None, {"record": record, "result": {}, "hints": {"season": 1}, "title": {
        "title_id": "tt1", "tmdb_id": 1, "name": "Show", "original_name": "Show", "media_type": "tv"},
        "confidence": 0.95, "method": "rule", "ranked": []})
    assert result["status"] == "review" and "目录分组仍需核对" in result["issues"]


def test_grouping_review_still_blocks_movies(monkeypatch):
    record = {**media_grouping.partition("/r/Arrival", [video("/r/Arrival/Arrival.2016.mkv")])[0], "grouping_review": True}
    result = recognition.finish(None, {"record": record, "result": {}, "hints": {"year": 2016}, "title": {
        "title_id": "tt2543164", "tmdb_id": 329865, "name": "Arrival", "original_name": "Arrival", "media_type": "movie", "year": 2016},
        "confidence": 0.95, "method": "rule", "ranked": []})
    assert result["status"] == "review" and result["issues"] == ["目录分组仍需核对"]
