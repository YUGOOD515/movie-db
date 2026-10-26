"""Validate discovery selections, then reuse the transactional media importer."""

import re
from contextlib import closing
from pathlib import PurePosixPath
from urllib.parse import quote

from connectors import alist
from connectors import tmdb
from models.database import connect_database
from queries.association import get_associated_paths
from services.media_discovery import canonical_path, scan_status
from services.media_import import MAX_BATCH_ITEMS, import_media_batch, load_title_catalog
from viewmodels.title_search import build_selection_viewmodel


def title_metadata(library_database, title_id):
    if not isinstance(title_id, str) or not re.fullmatch(r"tt[0-9]+", title_id):
        raise ValueError("IMDb ID 无效。")
    if tmdb.identify_title(title_id)["title_type"] == "episode":
        raise ValueError("请填写电影或剧集的 IMDb ID，不要填写单集 ID。")
    with closing(connect_database(library_database)) as db:
        catalog = load_title_catalog(db, title_id)
    title, episodes = catalog["root"], catalog["episodes"]
    return {**build_selection_viewmodel(title), "episodes": [
        {"title_id": episode["title_id"], "name": episode["display_title"],
         "season_number": episode["season_number"], "episode_number": episode["episode_number"]}
        for episode in episodes
    ]}


def _position(value, minimum):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,5}", str(value)):
        return None
    number = int(value)
    return number if number >= minimum else None


def check_association(library_database, payload, *, allow_existing=False):
    if not isinstance(payload, dict):
        raise ValueError("关联数据必须是 JSON 对象。")
    items = payload.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH_ITEMS:
        raise ValueError(f"请选择 1–{MAX_BATCH_ITEMS} 个视频。")
    title = title_metadata(library_database, payload.get("title_id"))
    discovered = {item["path"] for record in scan_status(library_database, prune=False)["records"] for item in record["files"]}
    with closing(connect_database(library_database)) as db:
        associated = get_associated_paths(db)
    positions = {}
    for episode in title["episodes"]:
        key = (episode["season_number"], episode["episode_number"])
        positions.setdefault(key, []).append(episode)
    rows, seen_paths, seen_positions = [], set(), set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条记录无效。")
        path = canonical_path(item.get("path"))
        error, target = "", None
        season = _position(item.get("season_number"), 0)
        episode = _position(item.get("episode_number"), 1)
        position = (season, episode)
        if path not in discovered or PurePosixPath(path).suffix.lower() not in alist.VIDEO_EXTENSIONS:
            error = "文件不在本次发现结果中，请刷新待关联列表。"
        elif path in associated and not allow_existing:
            error = "该文件已完成关联，请刷新待关联列表。"
        elif path in seen_paths:
            error = "同一文件重复选择。"
        elif title["media_type"] == "tv":
            matches = positions.get(position, [])
            if season is None or episode is None:
                error = "请填写有效季号和集号。"
            elif not matches:
                error = f"TMDB 中没有 S{season:02d} E{episode:02d}（或该集没有 IMDb ID），请选择可用季集。"
            elif len(matches) > 1:
                error = "该季集位置存在多条记录，暂无法唯一关联。"
            elif position in seen_positions:
                error = "多个文件对应同一季集，请检查或取消选择。"
            else:
                target = matches[0]
        else:
            target = title
        seen_paths.add(path)
        seen_positions.add(position)
        rows.append({"index": index, "path": path, "season_number": season,
                     "episode_number": episode, "target": target, "error": error})
    return {"title": title, "rows": rows, "valid": all(not row["error"] for row in rows)}


def _learn_alias(library_database, source_name, title):
    """记住“乱码目录名 → 作品”的人工确认结果，下次同名目录直接识别。"""
    from models import discovery_store as store
    from services.recognition import alias_key

    key = alias_key(source_name)
    if key:
        try:
            store.save_alias(library_database, key, {name: title.get(name) for name in
                                                     ("title_id", "name", "year", "media_type")})
        except Exception:
            pass


def complete_association(*, library_database, image_root, payload, on_progress=None,
                         probe_cache=None, on_probe=None, defer_enrichment=False,
                         commit_guard=None, allow_existing=False, expected_files=None):
    """校验后整批导入。on_progress(phase, done, total)：link 取直链、probe 读取媒体信息、write 写入。"""
    report = on_progress or (lambda *_: None)
    checked = check_association(library_database, payload, allow_existing=allow_existing)
    if not checked["valid"]:
        return checked
    token = alist.configured_token()
    is_series = checked["title"]["media_type"] == "tv"
    rows = checked["rows"]
    total = len(rows)
    report("link", 0, total)
    # Internal placeholders are only parsed locally. A worker obtains a fresh
    # signed/raw link immediately before probing, never while queued for minutes.
    items = [{"title_id": checked["title"]["title_id"],
              "alist_url": alist.get_origin() + "/d" + quote(row["path"], safe="/") + "?sign=pending",
              "season_number": str(row["season_number"]) if is_series else "",
              "episode_number": str(row["episode_number"]) if is_series else ""}
             for row in rows]

    def resolve(path):
        link = alist.get_file(path, token)
        expected = (expected_files or {}).get(path)
        if expected is not None:
            if expected[0] is not None and link.get("size") is not None and expected[0] != link["size"]:
                raise ValueError("源文件大小已变化，请重新扫描后确认。")
            if expected[1] and link.get("modified") and expected[1] != link["modified"]:
                raise ValueError("源文件已修改，请重新扫描后确认。")
        return link

    result = import_media_batch(library_database=library_database, image_root=image_root, items=items,
                                on_progress=on_progress, link_resolver=resolve,
                                probe_cache=probe_cache, on_probe=on_probe,
                                defer_enrichment=defer_enrichment, commit_guard=commit_guard)
    for failure in result["failures"]:
        index = failure["index"] - 1
        if 0 <= index < len(checked["rows"]):
            # Connector exceptions can contain signed URLs: keep the browser error safe.
            checked["rows"][index]["error"] = "导入失败，请检查视频可访问性和元数据服务后重试。"
    source = payload.get("source_name")
    if not result["failures"] and isinstance(source, str) and source.strip():
        _learn_alias(library_database, source, checked["title"])
    return {**checked, "valid": not result["failures"],
            "imported": len(result["successes"]),
            "retry_after": max((failure.get("retry_after", 0) for failure in result["failures"]), default=0),
            "enrichment_retry_after": max((success.get("enrichment_retry_after", 0) for success in result["successes"]), default=0),
            "enrichment_warning": any(success.get("enrichment_warning") for success in result["successes"]),
            "error": "批量导入失败，全部内容均未写入。" if result["failures"] else ""}
