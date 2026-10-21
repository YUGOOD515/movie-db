"""把待关联的目录或文件识别成 TMDB 作品，并预先算好每个视频的季集位置。

识别顺序由便宜到昂贵：人工确认过的别名 → 库内已有作品 → 规则清洗后搜索 TMDB
→（可选）AI 推断片名后再用 TMDB 校验。结果只是建议，入库仍需用户确认。
"""

from __future__ import annotations

import hashlib
import math
import re
from contextlib import closing
from difflib import SequenceMatcher
from pathlib import PurePosixPath

import requests

from connectors import llm, tmdb
from models import discovery_store as store
from models.database import connect_database
from services.name_cleaning import (
    english_title, folder_hints, guess_position, has_episode_marker, normalize,
)

VERSION = 3
READY = 0.8          # 高于此置信度且季集完整时，预选为“可直接确认”
AI_THRESHOLD = 0.75  # 规则结果低于此值时才请求 AI
AI_CEILING = 0.9

SYSTEM_PROMPT = """你是影视资源整理助手。网盘分享的目录名常被故意写乱以躲避审查，请推断它对应的正式作品。
常见手法：首字母或数字前缀（“R 片名”）、汉字间插入干扰符（“鱿.鱼.游.戏”“挽z救z计z划”“破か地か獄”）、
同音或近音字替换（“任渔”→“人鱼”）、形近字或加偏旁（“人苼苆剨朮”→“人生切割术”）、拼音或字母代替汉字（“小F”→“小芳”）、
繁体字，以及夹杂画质、字幕、音轨、体积等说明。文件名中的英文片名、年份、季集信息通常更可靠。
只输出 JSON：{"kind":"movie|tv|other","candidates":[{"title":"中文正式片名","original_title":"原名或英文名","year":2024,"reason":"一句话依据"}]}
candidates 最多 3 个，按可能性排序；不是电影或剧集（软件、素材、综艺花絮等）时 kind 为 other 且 candidates 为空。"""


def signature(record: dict) -> str:
    files = sorted(item["path"] for item in record["files"])
    digest = hashlib.sha1("\n".join([record["name"], *files]).encode("utf-8")).hexdigest()[:16]
    return f"v{VERSION}:{digest}:{int(llm.is_configured())}"


def alias_key(name: str) -> str:
    return normalize(name)[:120]


def _similarity(left: str, right: str) -> float:
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # 片名被截断或带副标题（“沙丘2”与“沙丘：第二部”）时给部分分。
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        ratio = max(ratio, 0.7 + 0.2 * min(len(a), len(b)) / max(len(a), len(b)))
    return ratio


def guess_media_type(record: dict, hints: dict) -> str | None:
    files = record["files"]
    if hints.get("season") is not None or any(has_episode_marker(item["name"]) for item in files):
        return "tv"
    if len(files) >= 3:
        return "tv"
    if len(files) == 1:
        return "movie"
    return None


def _candidate(result: dict, media_type: str) -> dict:
    date = result.get("release_date" if media_type == "movie" else "first_air_date") or ""
    return {
        "tmdb_id": result["id"], "media_type": media_type,
        "name": result.get("title" if media_type == "movie" else "name") or "",
        "original_name": result.get("original_title" if media_type == "movie" else "original_name") or "",
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "popularity": result.get("popularity") or 0,
    }


def _score(candidate: dict, query: str, year: int | None, expected: str | None) -> float:
    similarity = max(_similarity(query, candidate["name"]), _similarity(query, candidate["original_name"]))
    score = similarity * 0.85
    if year and candidate["year"]:
        gap = abs(year - candidate["year"])
        # 剧集目录上的年份常是某一季的年份，偏差惩罚更轻。
        score += 0.08 if gap == 0 else 0.03 if gap == 1 else (-0.04 if candidate["media_type"] == "tv" else -0.15)
    if expected:
        score += 0.05 if candidate["media_type"] == expected else -0.1
    score += min(0.05, math.log10(candidate["popularity"] + 1) / 60)
    return round(max(0.0, min(score, 1.0)), 3)


def _search(queries: list[str], year: int | None, expected: str | None) -> list[dict]:
    types = [expected] if expected else ["tv", "movie"]
    scored = {}
    for query in queries:
        for media_type in types:
            results = tmdb.search_media(query[:100], media_type, 1).get("results") or []
            for result in results[:8]:
                candidate = _candidate(result, media_type)
                candidate["score"] = _score(candidate, query, year, expected)
                key = (media_type, candidate["tmdb_id"])
                if key not in scored or scored[key]["score"] < candidate["score"]:
                    scored[key] = candidate
        best = max((item["score"] for item in scored.values()), default=0)
        if best >= 0.95:
            break
    ranked = sorted(scored.values(), key=lambda item: -item["score"])
    # 两个不同作品得分几乎相同（翻拍、同名）时降低置信度，交给人工核对。
    if len(ranked) >= 2 and ranked[0]["score"] - ranked[1]["score"] < 0.04:
        top = ranked[0]["score"]
        ranked = [{**item, "score": round(item["score"] - 0.12, 3)} if top - item["score"] < 0.04 else item
                  for item in ranked]
    return ranked


def _ask_ai(record: dict, hints: dict, english: str | None) -> dict:
    samples = [item["name"] for item in sorted(record["files"], key=lambda item: item["path"])[:8]]
    lines = [f"目录或文件名：{record['name']}", f"视频数量：{len(record['files'])}",
             "示例文件名：", *[f"- {name}" for name in samples]]
    if hints.get("queries"):
        lines.append(f"规则清洗结果（可能不准）：{'、'.join(hints['queries'][:3])}")
    if english:
        lines.append(f"文件名中的英文名：{english}")
    answer = llm.complete_json(SYSTEM_PROMPT, "\n".join(lines))
    kind = answer.get("kind") if answer.get("kind") in ("movie", "tv", "other") else None
    candidates = []
    for item in (answer.get("candidates") or [])[:3]:
        if isinstance(item, dict) and isinstance(item.get("title"), str) and item["title"].strip():
            year = item.get("year")
            candidates.append({"title": item["title"].strip()[:100],
                               "original_title": str(item.get("original_title") or "").strip()[:100],
                               "year": int(year) if str(year).isdigit() else None})
    return {"kind": kind, "candidates": candidates}


def _verify_ai(answer: dict, expected: str | None) -> list[dict]:
    media_type = answer["kind"] if answer["kind"] in ("movie", "tv") else expected
    verified = []
    for guess in answer["candidates"]:
        queries = [value for value in (guess["title"], guess["original_title"]) if value]
        for candidate in _search(queries, guess["year"], media_type)[:2]:
            candidate["score"] = round(min(AI_CEILING, candidate["score"]), 3)
            verified.append(candidate)
    verified.sort(key=lambda item: -item["score"])
    return verified


def _library_titles(library_database) -> list[dict]:
    with closing(connect_database(library_database)) as db:
        rows = db.execute("""
            SELECT title_id, display_title, primary_title, original_title, start_year, title_type, tmdb_id
              FROM title_basic WHERE title_type IN ('movie', 'series')
        """).fetchall()
    return [dict(row) for row in rows]


def _library_match(titles: list[dict], queries: list[str], expected: str | None) -> dict | None:
    wanted = {normalize(query) for query in queries if len(normalize(query)) >= 2}
    matches = []
    for title in titles:
        names = {normalize(title[key] or "") for key in ("display_title", "primary_title", "original_title")}
        if wanted & names:
            media_type = "movie" if title["title_type"] == "movie" else "tv"
            if expected and media_type != expected:
                continue
            matches.append(title)
    if len(matches) != 1:
        return None
    title = matches[0]
    return {"title_id": title["title_id"], "name": title["display_title"], "year": title["start_year"],
            "media_type": "movie" if title["title_type"] == "movie" else "tv", "tmdb_id": title["tmdb_id"]}


def _resolve(candidate: dict) -> dict | None:
    summary = tmdb.fetch_title_summary(candidate["media_type"], candidate["tmdb_id"])
    if not re.fullmatch(r"tt[0-9]+", summary["title_id"] or ""):
        return None
    tmdb.remember_media_identity(summary["title_id"], candidate["media_type"], candidate["tmdb_id"])
    return {"title_id": summary["title_id"], "name": summary["display_title"] or candidate["name"],
            "year": summary["start_year"] or candidate["year"], "media_type": candidate["media_type"],
            "tmdb_id": candidate["tmdb_id"]}


def map_positions(library_database, record: dict, title: dict, season_hint: int | None) -> dict:
    """为剧集的每个文件推测季集，并检查 TMDB 中是否都有唯一对应的单集。"""
    from services.media_association import title_metadata

    metadata = title_metadata(library_database, title["title_id"])
    available = {}
    for episode in metadata["episodes"]:
        key = (episode["season_number"], episode["episode_number"])
        available[key] = available.get(key, 0) + 1
    seasons = sorted({season for season, _ in available if season})
    default = season_hint if season_hint is not None else (seasons[0] if len(seasons) == 1 else 1)
    positions, matched, copies = {}, 0, {}
    for item in record["files"]:
        season, episode = guess_position(item["path"], season_hint=season_hint)
        season = default if season is None else season
        positions[item["path"]] = [season, episode]
        key = (season, episode)
        if episode is not None and available.get(key) == 1:
            matched += 1
            copies[key] = copies.get(key, 0) + 1
    # 同一集的多个版本（4K 与 1080p 等）可以分批关联，不算未匹配。
    return {"positions": positions, "coverage": round(matched / max(1, len(record["files"])), 3),
            "versions": max(copies.values(), default=1), "episodes": len(copies)}


def recognize(library_database, record: dict, *, titles: list[dict] | None = None, use_ai: bool = True) -> dict:
    """单条待关联记录 → 识别结果（不写库）。网络错误向上抛出，由调用方决定是否重试。"""
    is_file = len(record["files"]) == 1 and record["files"][0]["path"] == record["path"]
    source = PurePosixPath(record["name"]).stem if is_file else record["name"]
    hints = folder_hints(source)
    english = english_title([item["path"] for item in record["files"]])
    expected = guess_media_type(record, hints)
    queries = list(dict.fromkeys([*hints["queries"][:3], *([english] if english else [])]))
    result = {"status": "unknown", "confidence": 0, "method": "", "query": (queries or [source])[0],
              "media_type": expected, "title": None, "alternatives": [], "positions": {}, "coverage": None,
              "not_media": False}

    title, confidence, method, alternatives = None, 0.0, "", []
    suggestion = record.get("suggestion")
    if suggestion:
        title = {key: suggestion.get(key) for key in ("title_id", "name", "year", "media_type")}
        title["tmdb_id"] = suggestion.get("tmdb_id")
        confidence, method = 0.98, "library"
        hints = {**hints, "season": suggestion.get("season_number") or hints["season"]}
    if title is None:
        learned = store.find_alias(library_database, alias_key(record["name"]))
        if learned:
            title, confidence, method = learned, 0.97, "learned"
    if title is None:
        found = _library_match(titles if titles is not None else _library_titles(library_database), queries, expected)
        if found:
            title, confidence, method = found, 0.9, "library"
    if title is None:
        ranked = _search(queries, hints["year"], expected) if queries else []
        if (not ranked or ranked[0]["score"] < AI_THRESHOLD) and use_ai and llm.is_configured():
            try:
                answer = _ask_ai(record, hints, english)
            except (requests.RequestException, ValueError, KeyError, TypeError):
                answer = None
            if answer and answer["kind"] == "other" and not answer["candidates"]:
                result["not_media"] = True
            elif answer:
                verified = _verify_ai(answer, expected)
                if verified and (not ranked or verified[0]["score"] > ranked[0]["score"]):
                    ranked, method = verified + ranked, "ai"
        seen = set()
        for candidate in ranked:
            key = (candidate["media_type"], candidate["tmdb_id"])
            if key in seen:
                continue
            seen.add(key)
            if title is None:
                resolved = _resolve(candidate)
                if resolved:
                    title, confidence = resolved, candidate["score"]
                    method = method or "rule"
                continue
            alternatives.append({key: candidate[key] for key in ("tmdb_id", "media_type", "name", "year")})
            if len(alternatives) >= 3:
                break

    if title is None:
        return result
    if not title.get("tmdb_id"):
        pool = titles if titles is not None else _library_titles(library_database)
        known = next((item for item in pool if item["title_id"] == title["title_id"]), None)
        title = {**title, "tmdb_id": known["tmdb_id"] if known else None}
    result.update(title=title, confidence=round(confidence, 3), method=method, alternatives=alternatives,
                  media_type=title["media_type"])
    complete = True
    if title["media_type"] == "tv":
        try:
            mapped = map_positions(library_database, record, title, hints["season"])
        except ValueError:
            mapped = {"positions": {}, "coverage": None}
        result.update(mapped)
        complete = mapped["coverage"] == 1
    result["status"] = "ready" if confidence >= READY and complete else "review"
    return result
