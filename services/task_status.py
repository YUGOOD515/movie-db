"""Aggregate lightweight per-title tasks for the global sidebar."""
from collections import defaultdict
import json
import time

from connectors import events, throttle
from queries import tasks
from services import media_discovery, rating_sync, task_progress

STAGES = {"metadata": "资料", "probe": "探测", "write": "入库", "image": "补图", "rating": "评分", "derive": "预览"}


def _group(rows, now, names):
    first = rows[0]
    active = [row for row in rows if row["state"] in ("running", "queued")]
    total = sum(row["total"] for row in rows)
    core = sum(row["total"] if row["state"] == "complete" else row["progress"].get("core", 0) for row in rows)
    failed = sum(max(0, row["total"] - row["progress"].get("core", 0)) for row in rows if row["state"] == "failed")
    cancelled = any(row["state"] == "cancelled" for row in rows)
    incomplete = any(row["warning"] or row["state"] == "failed" for row in rows)
    state = "running" if active else "failed" if failed and not core else "partial" if incomplete else "cancelled" if cancelled else "complete"
    stages = defaultdict(lambda: {"done": 0, "total": 0, "failed": 0})
    current, issues, waits = [], [], {}
    for row in rows:
        progress = row["progress"]
        for key, value in progress.get("stages", {}).items():
            for field in ("done", "total", "failed"):
                stages[key][field] += value.get(field, 0)
        if row in active:
            current.extend(progress.get("active", []))
            waits.update({key: value for key, value in progress.get("waits", {}).items() if (value.get("until") or 0) > now})
        for issue in progress.get("issues", []):
            safe = {key: events.safe_text(value) for key, value in issue.items()}
            if safe not in issues:
                issues.append(safe)
        if row["error"] and row["state"] == "failed" and not progress.get("issues"):
            issues.append({"reason": events.safe_text(row["error"])})
    waiting = active and all(row["state"] == "queued" and row["next_at"] > now for row in active)
    phase, done, count = "metadata", 0, 0
    if waiting:
        state, phase = "waiting", "retry"
    elif active and all(row["state"] == "queued" for row in active):
        state, phase = "queued", "queued"
    elif active:
        if core >= total:
            phase = next((key for key in ("image", "rating", "derive") if any(item["stage"] == key for item in current)), "enrich")
        elif any(item["stage"] == "probe" for item in current) or stages["probe"]["total"]:
            phase = "probe"
            if all(row["progress"].get("probe", 0) >= row["total"] for row in active):
                phase = "write"
        if phase in stages:
            done, count = stages[phase]["done"], stages[phase]["total"]
        if phase == "probe":
            done, count = sum(row["total"] if row["state"] == "complete" else row["progress"].get("probe", 0) for row in rows), total
    else:
        phase, done, count = state, core, total
    name = next((row["progress"].get("name") or row["name"] for row in rows if row["progress"].get("name") or row["name"]), names.get(first["title_id"], first["title_id"]))
    active_files = list(dict.fromkeys(item["label"] for item in current if item["stage"] == "probe" and item.get("label")))
    return {"id": f'import:{first["title_id"]}:{first["batch"]}', "kind": "import", "name": name,
            "state": state, "phase": phase, "done": done, "total": count, "core": core, "files": total,
            "current": active_files[:3], "current_more": max(0, len(active_files) - 3),
            "stages": [{"key": key, "name": STAGES[key], **value} for key, value in stages.items() if key in STAGES and (value["total"] or value["done"])],
            "issues": issues[-8:], "waits": waits,
            "retry_at": min((row["next_at"] for row in active if row["next_at"] > now), default=0),
            "attempts": max(row["attempts"] for row in rows), "failed": failed,
            "cancel_ids": [row["job_id"] for row in active],
            "retry_ids": [row["job_id"] for row in rows if row["state"] in ("failed", "cancelled") or row["warning"]],
            "jobs": [{"id": row["job_id"], "state": row["state"], "core": row["total"] if row["state"] == "complete" else row["progress"].get("core", 0),
                      "warning": bool(row["warning"]), "error": events.safe_text(row["error"])} for row in rows],
            "updated_at": max(row["updated_at"] for row in rows)}


def snapshot(database):
    now = time.time()
    rows = tasks.import_tasks(database)
    live = task_progress.snapshots()
    groups = defaultdict(list)
    for row in rows:
        row["progress"] = live.get(row["job_id"]) or json.loads(row["progress"])
        groups[(row["title_id"], row["batch"])].append(row)
    rating = rating_sync.get_rating_refresh_status()
    ids = {row["title_id"] for row in rows if not row["name"] and not row["progress"].get("name")}
    if rating.get("current_title_id"):
        ids.add(rating["current_title_id"])
    names = tasks.title_names(database, ids)
    posters = tasks.title_posters(database, {title_id for title_id, _ in groups})
    entries = [{**_group(group, now, names), "poster": posters.get(title_id)} for (title_id, _), group in groups.items()]
    scan = media_discovery.scan_summary(database)
    from services import imdb_identity
    index = imdb_identity.status()
    if index.get("started_at"):
        building = index.get("phase") == "build"
        detail_text = (f"已处理 {index.get('processed_rows', 0):,} 条" if building else
                       f"已下载 {index.get('downloaded', 0) / 1048576:.1f} MiB") if index["running"] else index.get("message", "")
        entries.append({"id": "imdb-episodes", "kind": "imdb", "name": "更新 IMDb 数据",
                        "phase": ("imdb-build" if building else "imdb-download") if index["running"] else "failed" if index.get("error") else "complete",
                        "state": "running" if index["running"] else "failed" if index.get("error") else "complete",
                        "done": 0, "total": 0, "detail": detail_text, "updated_at": index.get("finished_at", index["started_at"]),
                        "issues": [{"reason": index["error"]}] if index.get("error") else []})
    if scan["status"] != "idle":
        recognizing = scan["status"] == "recognizing"
        entries.append({"id": "scan", "kind": "scan", "name": "扫描媒体", "phase": "recognizing" if recognizing else "scan",
                        "state": "running" if scan["status"] in ("running", "recognizing") else "failed" if scan.get("error") else "partial" if scan["warnings"] else "complete",
                        "done": scan.get("recognized" if recognizing else "done") or 0,
                        "total": scan.get("to_recognize" if recognizing else "total") or 0,
                        "issues": [{"reason": events.safe_text(value)} for value in ([scan["error"]] if scan.get("error") else scan["warnings"])],
                        "detail": f'AI {scan.get("ai_pending") or 0} · 核对 {scan.get("verifying") or 0}' if recognizing else ""})
    if rating.get("running") or rating.get("total") or rating.get("douban_total") or rating.get("douban_message"):
        douban = rating.get("phase") == "douban"
        failed = rating.get("failed") or rating.get("douban_message")
        entries.append({"id": "rating", "kind": "rating", "name": "刷新评分", "phase": "rating",
                        "state": "running" if rating["running"] else "partial" if failed else "complete",
                        "done": rating.get("douban_completed" if douban else "completed") or 0,
                        "total": rating.get("douban_total" if douban else "total") or 0,
                        "detail": names.get(rating.get("current_title_id"), "豆瓣" if douban else ""),
                        "issues": [{"reason": events.safe_text(rating.get("douban_message") or f'{rating["failed"]} 部评分获取失败')}] if failed else []})
    services = {item["service"]: item for item in throttle.diagnostics()}
    for entry in entries:
        for key, value in entry.get("waits", {}).items():
            services.setdefault(key, {"service": key, "name": throttle.LABELS.get(key, key), **value})
    active = any(entry["state"] in ("running", "waiting", "queued") for entry in entries)
    return {"tasks": entries, "services": list(services.values()), "active": active}


def detail(database, task_id, title_url=None):
    """侧栏任务的详情（只在打开详情抽屉时读取文件列表与探测检查点）。"""
    from viewmodels.tasks import import_detail

    kind, _, rest = str(task_id or "").partition(":")
    title_id, _, batch = rest.partition(":")
    if kind != "import" or not title_id or not batch:
        return None
    rows = tasks.import_task_detail(database, title_id, batch)
    if not rows:
        return None
    live = task_progress.snapshots()
    for row in rows:
        row["payload"] = json.loads(row["payload"])
        row["probes"] = json.loads(row["probes"] or "{}")
        row["result"] = json.loads(row["result"]) if row["result"] else None
        row["progress"] = live.get(row["job_id"]) or json.loads(row["progress"])
        row.update(total=len(row["payload"]["items"]), name=row["payload"].get("name"), batch=batch,
                   warning=(row["result"] or {}).get("warning"), updated_at=0)
    summary = _group(rows, time.time(), tasks.title_names(database, {title_id}))
    brief = tasks.title_brief(database, title_id)
    return import_detail(summary, rows, brief, title_url(title_id) if title_url and brief else "")
