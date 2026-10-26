"""把待关联的目录或文件识别成 TMDB 作品，并预先算好每个视频的季集位置。

识别顺序由便宜到昂贵：人工确认过的别名 → 库内已有作品 → 规则清洗后搜索 TMDB
→（可选）AI 推断片名后再用 TMDB 校验。结果只是建议，入库仍需用户确认。
"""

from __future__ import annotations

import hashlib
import json
import math
import queue
import re
import sqlite3
import threading
import time
from contextlib import closing
from difflib import SequenceMatcher
from pathlib import PurePosixPath

import requests

from connectors import llm, throttle, tmdb
from models import discovery_store as store
from models.database import connect_database
from services.name_cleaning import (
    english_title, folder_hints, guess_position, has_episode_marker, normalize,
)

VERSION = 5
READY = 0.8          # 高于此置信度且季集完整时，预选为“可直接确认”
AI_THRESHOLD = 0.75  # 规则结果低于此值时才请求 AI
AI_CEILING = 0.9

# 批量与单条共用同一份固定提示词：DeepSeek 上下文缓存按前缀完全匹配命中，固定前缀越长、越稳定越省钱。
SYSTEM_PROMPT = """你是影视资源整理助手。网盘分享的目录名常被故意写乱以躲避审查，请推断每一项对应的正式作品。
常见手法：首字母或数字前缀（“R 片名”）、汉字间插入干扰符（“鱿.鱼.游.戏”“挽z救z计z划”“破か地か獄”）、
同音或近音字替换（“任渔”→“人鱼”）、形近字或加偏旁（“人苼苆剨朮”→“人生切割术”）、拼音或字母代替汉字（“小F”→“小芳”）、
繁体字，以及夹杂画质、字幕、音轨、体积等说明。文件名中的英文片名、年份、季集信息通常更可靠。

输入是 JSON：{"items":[{"id":"1","name":"目录或文件名","videos":12,"samples":["示例文件名"],"rules":["规则清洗结果，可能不准"],"english":"文件名中的英文名"}]}
逐项判断，只输出 JSON，items 必须覆盖输入的每个 id，顺序不限：
{"items":[{"id":"1","kind":"movie|tv|other","candidates":[{"title":"中文正式片名","original_title":"原名或英文名","year":2024}]}]}
candidates 最多 3 个，按可能性排序；不是电影或剧集（软件、课程、素材、综艺花絮等）时 kind 为 other 且 candidates 为空；
无法判断时 candidates 为空。year 不确定时填 null。不要输出解释。"""
# 每条记录的输出约 60–90 token；按条数给足上限，避免 JSON 被截断。
OUTPUT_TOKENS_PER_ITEM = 160


def signature(record: dict, *, ai: bool | None = None) -> str:
    """识别缓存键。ai=False 表示本次没能用上 AI（未配置、限流或调用失败），AI 可用后签名不再匹配会重识别。"""
    files = sorted(f"{item['path']}:{item.get('size')}:{item.get('modified')}" for item in record["files"])
    digest = hashlib.sha1("\n".join([record["name"], *files]).encode("utf-8")).hexdigest()[:16]
    used = llm.is_configured() if ai is None else ai
    return f"v{VERSION}:{digest}:{int(used)}"


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


# TMDB 查询的持久缓存（discovery.db）：配置 AI 后重识别、规则版本升级或“重新识别”时不必再搜一遍。
SEARCH_TTL = 7 * 86400
SUMMARY_TTL = 30 * 86400
MISSING_TTL = 86400  # 暂无 IMDb ID 的作品一天后再查，TMDB 补录后能及时用上


def _remembered(database, key: str, ttl, fetch):
    if database is None:
        return fetch()
    try:
        hit = store.cache_get(database, key)
    except sqlite3.Error:
        hit = None
    if hit is not None:
        return hit
    value = fetch()
    try:
        store.cache_put(database, key, value, ttl(value) if callable(ttl) else ttl)
    except sqlite3.Error:
        pass
    return value


def _search_results(database, query: str, media_type: str | None) -> list[dict]:
    """TMDB 搜索 → 候选列表。类型未知时用 /search/multi 一次搜电影和剧集，请求数减半。"""
    def fetch():
        if media_type:
            rows = [(media_type, result) for result in (tmdb.search_media(query, media_type, 1).get("results") or [])[:8]]
        else:
            rows = [(result["media_type"], result) for result in tmdb.search_multi(query).get("results") or []
                    if result.get("media_type") in ("movie", "tv")][:16]
        return [_candidate(result, kind) for kind, result in rows]
    return _remembered(database, f"search:{media_type or 'multi'}:{tmdb.LANGUAGE}:{query}", SEARCH_TTL, fetch)


def _search(queries: list[str], year: int | None, expected: str | None, database=None) -> list[dict]:
    scored = {}
    for query in queries:
        for found in _search_results(database, query[:100], expected):
            candidate = {**found, "score": _score(found, query, year, expected)}
            key = (candidate["media_type"], candidate["tmdb_id"])
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


def _ai_item(record: dict, hints: dict, english: str | None) -> dict:
    # 示例文件名取前 3 个加最后 1 个：同一剧集的文件名高度相似，多给只会增加输入 token。
    names = [item["name"] for item in sorted(record["files"], key=lambda item: item["path"])]
    samples = names if len(names) <= 4 else [*names[:3], names[-1]]
    item = {"name": record["name"], "videos": len(record["files"]), "samples": samples}
    if hints.get("queries"):
        item["rules"] = hints["queries"][:3]
    if english:
        item["english"] = english
    return item


def _normalize_answer(answer) -> dict | None:
    if not isinstance(answer, dict):
        return None
    kind = answer.get("kind") if answer.get("kind") in ("movie", "tv", "other") else None
    candidates = []
    for item in (answer.get("candidates") or [])[:3]:
        if isinstance(item, dict) and isinstance(item.get("title"), str) and item["title"].strip():
            year = item.get("year")
            candidates.append({"title": item["title"].strip()[:100],
                               "original_title": str(item.get("original_title") or "").strip()[:100],
                               "year": int(year) if str(year).isdigit() else None})
    return {"kind": kind, "candidates": candidates}


def ask_batch(items: list[dict], *, usage: dict | None = None) -> dict[str, dict]:
    """一次请求识别多条记录（合并请求）。items 为 _ai_item 结果，按位置编号 1..n。

    返回 {编号: 答案}；模型漏掉的编号不在结果里，由调用方单独重试。
    """
    numbered = [{"id": str(index), **item} for index, item in enumerate(items, 1)]
    user = json.dumps({"items": numbered}, ensure_ascii=False, separators=(",", ":"))
    answer = llm.complete_json(SYSTEM_PROMPT, user, usage=usage,
                               max_tokens=200 + OUTPUT_TOKENS_PER_ITEM * len(items))
    rows = answer.get("items")
    if not isinstance(rows, list):
        # 只问一条时，部分模型会省略外层 items 直接回答。
        rows = [{**answer, "id": "1"}] if len(items) == 1 and ("kind" in answer or "candidates" in answer) else []
    found = {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("id", "")).strip() in {item["id"] for item in numbered}:
            normalized = _normalize_answer(row)
            if normalized:
                found[str(row["id"]).strip()] = normalized
    return found


def _ask_ai(record: dict, hints: dict, english: str | None, *, usage: dict | None = None) -> dict:
    found = ask_batch([_ai_item(record, hints, english)], usage=usage)
    if "1" not in found:
        raise ValueError("AI 没有给出这一项的结果。")
    return found["1"]


def _verify_ai(answer: dict, expected: str | None, database=None) -> list[dict]:
    media_type = answer["kind"] if answer["kind"] in ("movie", "tv") else expected
    verified = []
    for guess in answer["candidates"]:
        queries = [value for value in (guess["title"], guess["original_title"]) if value]
        for candidate in _search(queries, guess["year"], media_type, database)[:2]:
            candidate["score"] = round(min(AI_CEILING, candidate["score"]), 3)
            verified.append(candidate)
    verified.sort(key=lambda item: -item["score"])
    return verified


def _library_titles(library_database) -> list[dict]:
    from queries.association import get_recognition_titles

    with closing(connect_database(library_database)) as db:
        return get_recognition_titles(db)


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


def _resolve(candidate: dict, database=None) -> dict | None:
    summary = _remembered(
        database, f"summary:{candidate['media_type']}:{candidate['tmdb_id']}",
        lambda value: SUMMARY_TTL if value.get("title_id") else MISSING_TTL,
        lambda: tmdb.fetch_title_summary(candidate["media_type"], candidate["tmdb_id"]))
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


def prepare(library_database, record: dict, *, titles: list[dict] | None = None, use_ai: bool = True) -> dict:
    """识别的第一阶段：规则与库内匹配。返回上下文；needs_ai 为 True 时应再请求 AI，然后交给 finish。"""
    is_file = len(record["files"]) == 1 and record["files"][0]["path"] == record["path"]
    source = PurePosixPath(record["name"]).stem if is_file else record["name"]
    hints = folder_hints(source)
    english = english_title([item["path"] for item in record["files"]])
    expected = guess_media_type(record, hints)
    queries = list(dict.fromkeys([*hints["queries"][:3], *([english] if english else [])]))
    result = {"status": "unknown", "confidence": 0, "method": "", "query": (queries or [source])[0],
              "media_type": expected, "title": None, "alternatives": [], "positions": {}, "coverage": None,
              "not_media": False}
    context = {"record": record, "result": result, "hints": hints, "english": english, "expected": expected,
               "titles": titles, "title": None, "confidence": 0.0, "method": "", "ranked": [], "needs_ai": False}

    suggestion = record.get("suggestion")
    if suggestion:
        title = {key: suggestion.get(key) for key in ("title_id", "name", "year", "media_type")}
        title["tmdb_id"] = suggestion.get("tmdb_id")
        context.update(title=title, confidence=0.98, method="library",
                       hints={**hints, "season": suggestion.get("season_number") or hints["season"]})
        return context
    learned = store.find_alias(library_database, alias_key(record["name"]))
    if learned:
        context.update(title=learned, confidence=0.97, method="learned")
        return context
    found = _library_match(titles if titles is not None else _library_titles(library_database), queries, expected)
    if found:
        context.update(title=found, confidence=0.9, method="library")
        return context
    ranked = _search(queries, hints["year"], expected, library_database) if queries else []
    context["ranked"] = ranked
    context["needs_ai"] = bool((not ranked or ranked[0]["score"] < AI_THRESHOLD) and use_ai and llm.is_configured())
    return context


def ai_item(context: dict) -> dict:
    return _ai_item(context["record"], context["hints"], context["english"])


def finish(library_database, context: dict, answer: dict | None = None) -> dict:
    """识别的第二阶段：合并 AI 答案（已用 TMDB 校验）、确定作品并推算季集。"""
    record, result, hints = context["record"], context["result"], context["hints"]
    title, confidence, method = context["title"], context["confidence"], context["method"]
    alternatives, ranked = [], list(context["ranked"])
    if title is None:
        if answer and answer["kind"] == "other" and not answer["candidates"]:
            result["not_media"] = True
        elif answer:
            verified = _verify_ai(answer, context["expected"], library_database)
            if verified and (not ranked or verified[0]["score"] > ranked[0]["score"]):
                ranked, method = verified + ranked, "ai"
        seen = set()
        for candidate in ranked:
            key = (candidate["media_type"], candidate["tmdb_id"])
            if key in seen:
                continue
            seen.add(key)
            if title is None:
                resolved = _resolve(candidate, library_database)
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
        pool = context["titles"] if context["titles"] is not None else _library_titles(library_database)
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


def ai_used(result: dict) -> bool:
    """写缓存用：AI 被跳过或失败的结果记为“未用 AI”，下次扫描会重新识别。"""
    return llm.is_configured() and not (result.get("ai_deferred") or result.get("ai_error"))


def recognize(library_database, record: dict, *, titles: list[dict] | None = None, use_ai: bool = True) -> dict:
    """单条待关联记录 → 识别结果（不写库）。TMDB 网络错误向上抛出；AI 出错只降级并写明原因。"""
    context = prepare(library_database, record, titles=titles, use_ai=use_ai)
    answer = None
    if context["needs_ai"]:
        try:
            answer = _ask_ai(record, context["hints"], context["english"])
        except throttle.RateLimitError:
            # 额度用尽：先用规则结果，签名记为“未用 AI”，额度恢复后的扫描会自动补识别。
            context["result"]["ai_deferred"] = True
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            context["result"]["ai_error"] = str(error) or "AI 请求失败。"
    return finish(library_database, context, answer)


# AI 批次凑满 LLM_BATCH_SIZE 或最早一条已等待这么久时就发出，规则还在跑时 AI 已经开工。
BATCH_WAIT = 1.0


def recognize_many(library_database, records: list[dict], *, titles: list[dict] | None = None,
                   on_result=None, on_progress=None, workers: int = 16) -> dict:
    """批量识别（流水线）：规则、AI、TMDB 核对三段同时进行。

    - 规则：workers 个线程并行清洗与搜索 TMDB（整体受 throttle 的 TMDB 速率约束），不需要 AI 的记录当场完成；
    - 合并请求：需要 AI 的记录边产生边攒批，每批最多 LLM_BATCH_SIZE 条（DeepSeek 默认 8），
      凑满或最早一条等待 BATCH_WAIT 秒即发出；共享固定的系统提示词前缀，利于命中上下文缓存；
    - 批次并发：同时 LLM_CONCURRENCY 批（DeepSeek 默认 64，遇到 429 自动减半再逐步恢复）；
    - 核对：AI 答案交给另一组线程用 TMDB 校验、推算季集，不占用 AI 并发；
    - 循环重试：整批失败（输出截断、JSON 不完整、5xx）时对半拆分再试，模型漏掉的条目单独重试一次；
    - 熔断：Key 无效 / 余额不足立即停止后续 AI 请求；限流到额度恢复前同样停止，结果记为“未用 AI”。
    on_result(record, result) 每完成一条调用一次；on_progress(progress) 在各阶段计数变化时调用，
    progress = {total, done, rules, ai, verifying}（ai 为排队或请求中的条数）。TMDB 未配置（RuntimeError）向上抛出。
    """
    from concurrent.futures import ThreadPoolExecutor

    stats = {"needs_ai": 0, "requests": 0, "ai": 0, "deferred": 0, "failed": 0, "error": "", "usage": {}}
    streaming = not isinstance(records, (list, tuple))
    progress = {"total": 0 if streaming else len(records), "done": 0, "rules": 0, "ai": 0, "verifying": 0}
    lock = threading.Lock()
    stopped: list[Exception] = []  # 熔断原因；非空后不再发起新的 AI 请求
    errors: list[RuntimeError] = []
    arrivals: queue.Queue = queue.Queue()
    submitted, ended = object(), object()

    def bump(**changes):
        with lock:
            for name, delta in changes.items():
                progress[name] += delta
            snapshot = dict(progress)
        if on_progress:
            on_progress(snapshot)

    def emit(context, answer=None):
        try:
            result = finish(library_database, context, answer)
        except RuntimeError as error:
            errors.append(error)
            return None
        except Exception:
            return None  # TMDB 网络错误：不缓存，下次扫描重试
        finally:
            bump(done=1)
        if result.get("method") == "ai":
            with lock:
                stats["ai"] += 1
        if on_result:
            on_result(context["record"], result)
        return result

    def first_stage(record):
        context = None
        try:
            if not errors:
                context = prepare(library_database, record, titles=titles)
        except RuntimeError as error:
            errors.append(error)
        except Exception:
            bump(done=1)  # TMDB 网络错误：不缓存，下次扫描重试
        if context is not None and context["needs_ai"]:
            bump(ai=1)
            arrivals.put(context)
        arrivals.put(None)  # 先放记录再放完成标记：主线程收齐标记时，需要 AI 的记录必然都已取到
        if context is not None and not context["needs_ai"]:
            emit(context)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="recognize-verify") as checks, \
         ThreadPoolExecutor(max_workers=max(1, llm.concurrency()), thread_name_prefix="recognize-ai") as asks, \
         ThreadPoolExecutor(max_workers=workers, thread_name_prefix="recognize-rule") as rules:

        def verify(context, answer=None):
            bump(ai=-1, verifying=1)

            def run():
                try:
                    emit(context, answer)
                finally:
                    bump(verifying=-1)
            checks.submit(run)

        def fail(contexts, error):
            key = "ai_deferred" if isinstance(error, throttle.RateLimitError) else "ai_error"
            with lock:
                stats["deferred" if key == "ai_deferred" else "failed"] += len(contexts)
                stats["error"] = stats["error"] or str(error)
            for context in contexts:
                context["result"][key] = True if key == "ai_deferred" else (str(error) or "AI 请求失败。")
                verify(context)

        def ask(contexts, depth=0, transient=0):
            """请求一批并把答案交给核对线程；拆分与漏项重试都在这里循环完成。"""
            if stopped:
                fail(contexts, stopped[0])
                return
            try:
                with lock:
                    stats["requests"] += 1
                found = ask_batch([ai_item(context) for context in contexts], usage=stats["usage"])
            except throttle.RateLimitError as error:
                stopped.append(error)
                fail(contexts, error)
                return
            except llm.LlmError as error:
                if error.fatal or not error.retry:
                    # Key 无效、余额不足、参数不被接受：每批都会同样失败，停止本轮 AI 请求。
                    stopped.append(error)
                    fail(contexts, error)
                    return
                if error.status is not None and error.status >= 500:
                    # Service outages are independent of batch size. Never turn
                    # one failed batch into a binary tree of failed requests.
                    if transient < 2:
                        time.sleep(throttle._backoff(transient))
                        ask(contexts, depth, transient + 1)
                    else:
                        stopped.append(error)
                        fail(contexts, error)
                    return
                if len(contexts) > 1 and depth < 4:
                    middle = len(contexts) // 2
                    ask(contexts[:middle], depth + 1)
                    ask(contexts[middle:], depth + 1)
                    return
                fail(contexts, error)
                return
            except requests.RequestException as error:
                if transient < 2:
                    time.sleep(throttle._backoff(transient))
                    ask(contexts, depth, transient + 1)
                else:
                    stopped.append(error)
                    fail(contexts, error)
                return
            except (ValueError, KeyError, TypeError) as error:
                if len(contexts) > 1 and depth < 4:
                    middle = len(contexts) // 2
                    ask(contexts[:middle], depth + 1)
                    ask(contexts[middle:], depth + 1)
                    return
                fail(contexts, error if str(error) else ValueError("AI 返回内容无法解析。"))
                return
            missing = []
            for index, context in enumerate(contexts, 1):
                answer = found.get(str(index))
                if answer is None:
                    missing.append(context)
                else:
                    verify(context, answer)
            if missing and len(contexts) > 1 and depth < 4:
                # 模型漏掉的条目：合成一批再问一次（只剩一条时即单条请求）。
                ask(missing, depth + 1)
            elif missing:
                fail(missing, ValueError("AI 没有给出这一项的结果。"))

        batches = []
        size = llm.batch_size()
        buffer: list[dict] = []
        oldest = 0.0

        def flush():
            nonlocal buffer
            if buffer:
                with lock:
                    stats["needs_ai"] += len(buffer)
                batches.append(asks.submit(ask, buffer))
                buffer = []

        capacity = threading.Semaphore(workers * 4)

        def produce():
            try:
                for record in records:
                    capacity.acquire()
                    if streaming:
                        bump(total=1)
                    arrivals.put(submitted)
                    future = rules.submit(first_stage, record)
                    future.add_done_callback(lambda _: capacity.release())
            except Exception as error:
                errors.append(RuntimeError(str(error)))
            finally:
                arrivals.put(ended)

        producer = threading.Thread(target=produce, name="recognize-feed", daemon=True)
        producer.start()
        remaining, open_input = 0, True
        while open_input or remaining:
            timeout = max(0.0, oldest + BATCH_WAIT - time.monotonic()) if buffer else None
            try:
                item = arrivals.get(timeout=timeout)
            except queue.Empty:
                flush()
                continue
            if item is submitted:
                remaining += 1
                continue
            if item is ended:
                open_input = False
                continue
            if item is None:
                remaining -= 1
                bump(rules=1)
                continue
            if not buffer:
                oldest = time.monotonic()
            buffer.append(item)
            if len(buffer) >= size:
                flush()
        if not errors:
            flush()
        for batch in batches:
            batch.result()
        producer.join()
    if errors:
        raise errors[0]
    return stats
