"""任务详情抽屉的页面形状：关联四步、逐个文件、之后补齐的资料。

前端（task-center.js）只负责按这里给出的 state / text 渲染，不再自己推断阶段。
state 取值：done 已完成、active 进行中、pending 未开始、failed 失败、skipped 无需处理。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from viewmodels import media_labels

CORE_STEPS = (("check", "核对资料"), ("link", "获取链接"), ("probe", "读取参数"), ("write", "写入媒体库"))
ENRICH_STEPS = (("image", "海报与剧照"), ("rating", "评分"), ("derive", "预览图"))
STATE_TEXT = {"running": "关联中", "queued": "排队中", "waiting": "等待重试", "complete": "已完成",
              "partial": "已关联，部分资料未补齐", "failed": "关联失败", "cancelled": "已停止"}
PILL = {"running": "busy", "queued": "warn", "waiting": "warn", "complete": "ok", "partial": "warn",
        "failed": "fail", "cancelled": "off"}


def _probe_summary(probe: dict | None) -> str:
    if not probe:
        return ""
    width, height = probe.get("video_width"), probe.get("video_height")
    parts = [
        probe.get("video_dynamic_range") or "",
        f"{width}×{height}" if width and height else "",
        media_labels.codec(probe.get("video_codec")),
        media_labels.channels(probe),
    ]
    return " · ".join(part for part in parts if part)


def _position(item: dict) -> str:
    season, episode = item.get("season_number"), item.get("episode_number")
    if season in (None, "") or episode in (None, ""):
        return ""
    return f"S{int(season):02}E{int(episode):02}"


def _files(rows: list[dict], active_labels: set[str]) -> list[dict]:
    files = []
    for row in rows:
        probes = row["probes"]
        errors = {item.get("path"): item.get("error") for item in (row["result"] or {}).get("rows", []) if item.get("error")}
        for item in row["payload"]["items"]:
            path = item["path"]
            name = PurePosixPath(path).name
            position = _position(item)
            summary = _probe_summary(probes.get(path))
            if row["state"] == "complete":
                state, detail = "done", summary or "已关联"
            elif path in errors:
                state, detail = "failed", errors[path]
            elif row["state"] == "failed":
                state, detail = "skipped", "同批文件出错，本文件未写入"
            elif summary:
                state, detail = "done", summary
            elif (position or name) in active_labels:
                state, detail = "active", "正在读取参数…"
            else:
                state, detail = "pending", "等待中"
            season = item.get("season_number")
            files.append({"name": name, "position": position, "season": int(season) if position else None,
                          "state": state, "detail": detail, "written": row["state"] == "complete"})
    return files


def _outcome(files: list[dict], series: bool) -> str:
    """出错时的一句话：部分失败写“已入库 10 集，2 集失败”，全部失败写“2 集失败”。"""
    written = sum(file["written"] for file in files)
    failed = sum(file["state"] in ("failed", "skipped") for file in files)
    if not failed:
        return ""
    unit = "集" if series else "个视频"
    return f"已入库 {written} {unit}，{failed} {unit}失败" if written else f"{failed} {unit}失败"


def _core_steps(summary: dict, rows: list[dict]) -> list[dict]:
    total = summary["files"] or 0
    running = summary["state"] == "running"
    finished = summary["state"] in ("complete", "partial") or (total and summary["core"] >= total)
    links = sum(row["progress"].get("link", 0) if row["state"] != "complete" else len(row["payload"]["items"]) for row in rows)
    probes = sum(len(row["probes"]) if row["state"] != "complete" else len(row["payload"]["items"]) for row in rows)
    checked = finished or links or probes or any(row["progress"].get("stages", {}).get("metadata", {}).get("done") for row in rows)
    done = {"check": bool(checked), "link": finished or (total and links >= total),
            "probe": finished or (total and probes >= total), "write": bool(finished)}
    counts = {"link": f"{min(links, total)}/{total}", "probe": f"{min(probes, total)}/{total}"}
    steps, blocked = [], False
    for key, label in CORE_STEPS:
        if done[key]:
            state = "done"
        elif blocked:
            state = "pending"
        else:
            blocked = True
            state = "failed" if summary["state"] == "failed" else "active" if running else "pending"
        steps.append({"key": key, "label": label, "state": state,
                      "count": counts.get(key, "") if state in ("active", "failed") else ""})
    return steps


def _enrich(summary: dict) -> list[dict]:
    stages = {stage["key"]: stage for stage in summary.get("stages", [])}
    core_done = summary["state"] in ("complete", "partial") or (summary["files"] and summary["core"] >= summary["files"])
    items = []
    for key, label in ENRICH_STEPS:
        stage = stages.get(key)
        if not stage or not (stage["total"] or stage["done"]):
            state, text = ("skipped", "无需补齐") if core_done and summary["state"] != "running" else ("pending", "关联后开始")
        elif stage["failed"]:
            state, text = "failed", f"{stage['failed']} 项未获取"
        elif stage["total"] and stage["done"] < stage["total"]:
            state, text = ("active" if summary["state"] == "running" else "pending"), f"{stage['done']}/{stage['total']}"
        else:
            state, text = "done", f"{stage['done']} 项" if stage["done"] > 1 else "已完成"
        items.append({"key": key, "label": label, "state": state, "text": text})
    return items


def import_detail(summary: dict, rows: list[dict], brief: dict | None, title_url: str = "") -> dict:
    """summary 是 task_status 汇总的同一任务；rows 已解析 payload / probes / result / progress。"""
    active_labels = set(summary.get("current") or [])
    media_type = (brief or {}).get("title_type")
    meta = [{"movie": "电影", "series": "剧集", "episode": "剧集"}.get(media_type, ""),
            str((brief or {}).get("start_year") or ""), f"{summary['files']} 个视频" if summary["files"] else ""]
    files = _files(rows, active_labels)
    series = any(file["position"] for file in files)
    current = next((file for file in files if file["state"] == "active"), None)
    return {
        "id": summary["id"],
        "name": (brief or {}).get("display_title") or summary["name"],
        "meta": " · ".join(part for part in meta if part),
        "poster": (brief or {}).get("poster_path"),
        "media_type": media_type,
        "state": summary["state"],
        "state_text": STATE_TEXT.get(summary["state"], ""),
        "pill": PILL.get(summary["state"], "off"),
        "steps": _core_steps(summary, rows),
        "files": files,
        "series": series,
        "current": " · ".join(part for part in (current["position"], current["name"]) if part) if current else "",
        "outcome": _outcome(files, series),
        "enrich": _enrich(summary),
        "issues": [" · ".join(part for part in (issue.get("label"), issue.get("reason")) if part)
                   for issue in summary.get("issues", []) if issue.get("reason")],
        "title_url": title_url if brief else "",
        "playable": bool(summary["core"]) and summary["core"] >= summary["files"],
    }
