from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
import threading
from concurrent.futures import as_completed, wait
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import requests

from connectors import events, imdb_ratings, media_probe, throttle, tmdb
from models.database import connect_database, connect_writable
from models.library import import_media_bundle, enrich_media_bundle
from queries.association import get_douban_id, get_existing_artwork_slots, get_file_targets, get_series_episodes
from services.rating_sync import fetch_douban_rating, fetch_title_rating
from services.image_tones import ensure_tones, compute_tone, merge_tones, wants_tone
from services.thumbnails import ensure_thumbnail, normalize_image_path, generate_thumbnail, get_target_size_for_path
from services import import_runtime as runtime
from services import media_source


TITLE_ID_PATTERN = re.compile(r"tt[0-9]+")
MAX_BATCH_ITEMS = 100
logger = logging.getLogger(__name__)


class MediaImportError(ValueError):
    pass


def _parse_position(season_value: str, episode_value: str) -> tuple[int, int]:
    try:
        season_number = int(season_value)
        episode_number = int(episode_value)
    except (TypeError, ValueError) as error:
        raise MediaImportError("Series Title ID 必须填写有效的季号和集号。") from error
    if season_number < 0 or episode_number < 1:
        raise MediaImportError("季号必须大于等于 0，集号必须大于等于 1。")
    return season_number, episode_number


def _validate_episode_position(season_value: str, episode_value: str, episode: dict) -> None:
    if season_value.strip() or episode_value.strip():
        expected = (episode["season_number"], episode["episode_number"])
        if _parse_position(season_value, episode_value) != expected:
            raise MediaImportError("填写的季集号与 Episode Title ID 不一致。")


def load_title_catalog(library_db: sqlite3.Connection, title_id: str) -> dict:
    """电影或剧集的完整元数据：TMDB 为主，库内已有的季集关系优先沿用。

    返回 metadata（TMDB 原样结构）与 episodes（可录入单集，按季集排序；
    TMDB 已没有、但库里已有的单集也保留为可选目标，只是不重写其资料）。
    """
    known = get_series_episodes(library_db, title_id)
    metadata = tmdb.build_media_metadata(
        title_id,
        known_episode_ids={
            (row["season_number"], row["episode_number"]): row["title_id"] for row in known
        },
    )
    root = metadata.get("title_basic") or {}
    if root.get("title_id") != title_id or root.get("title_type") not in ("movie", "series"):
        raise MediaImportError("TMDB 中没有可录入的电影或剧集。")
    episodes = {
        (item["season_number"], item["episode_number"]): {
            **item["title_basic"],
            "season_number": item["season_number"],
            "episode_number": item["episode_number"],
        }
        for item in metadata.get("episodes") or []
    }
    for row in known:
        episodes.setdefault((row["season_number"], row["episode_number"]), row)
    return {"metadata": metadata, "root": root, "episodes": [episodes[key] for key in sorted(episodes)]}


def _new_artwork(db: sqlite3.Connection, artwork: list[dict], image_root=None) -> list[dict]:
    if not artwork:
        return []

    title_ids = list(dict.fromkeys(image["title_id"] for image in artwork))
    existing = get_existing_artwork_slots(db, title_ids)
    with runtime.write_lock:
        return [image for image in artwork
                if (image["title_id"], image["artwork_type"], image["position"]) not in existing
                or (image_root is not None and not _artwork_path(image_root, image["local_path"]).is_file())]


def _artwork_path(root: Path, local_path: str) -> Path:
    path = normalize_image_path(local_path)
    if path is None:
        raise MediaImportError(f"图片路径无效：{local_path}")
    return root.joinpath(*path.parts[1:])


def _stage_new_artwork(
    artwork: list[dict],
    image_root: Path,
    staging_root: Path,
    on_download=None,
) -> tuple[list[dict], list[tuple[Path, Path]]]:
    if not artwork:
        return [], []

    pending = []
    successful = []
    staged_files = []
    for image in artwork:
        target = _artwork_path(image_root, image["local_path"])
        with runtime.write_lock:
            exists = target.is_file()
        if exists:
            successful.append(image)
            events.emit("end", stage="image", key=image["local_path"], failed=False)
        else:
            pending.append(image)

    if pending:
        options = {"on_download": on_download} if on_download else {}
        tmdb.download_metadata_images({"title_artwork": pending, "episodes": []}, staging_root, **options)
        for image in pending:
            source = _artwork_path(staging_root, image["local_path"])
            if not source.is_file():
                continue
            target = _artwork_path(image_root, image["local_path"])
            successful.append(image)
            staged_files.append((source, target))

    return successful, staged_files


def _stage_cast_images(
    cast: list[dict],
    image_root: Path,
    staging_root: Path,
) -> tuple[list[dict], list[tuple[Path, Path]]]:
    images, staged_files = _stage_new_artwork(
        [person for person in cast if person.get("local_path")],
        image_root,
        staging_root,
    )
    available = {person["tmdb_person_id"] for person in images}
    return [
        person
        if not person.get("local_path") or person["tmdb_person_id"] in available
        else {**person, "local_path": None}
        for person in cast
    ], staged_files


def _publish_staged_files(
    staged_files: list[tuple[Path, Path]],
    published_files: list[Path],
) -> None:
    for source, target in staged_files:
        if target.is_file():
            continue
        if target.exists():
            raise OSError(f"图片目标路径不是文件：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        published_files.append(target)


def _remove_moved_files(paths: list[Path]) -> None:
    for path in reversed(paths):
        path.unlink(missing_ok=True)


@dataclass
class ImportContext:
    """同一作品在一个批次内共用的元数据，只构建和写入一次。"""

    root: dict
    titles: list[dict]
    relations: list[dict]
    artwork: list[dict]
    cast: list[dict]
    rating_result: dict
    episodes_by_position: dict[tuple[int, int], dict] = field(default_factory=dict)
    episodes_by_id: dict[str, dict] = field(default_factory=dict)
    staged_files: list[tuple[Path, Path]] = field(default_factory=list)
    images_prepared: bool = False
    rating_future: object = None
    douban_future: object = None
    tone_data: dict = field(default_factory=dict)
    image_future: object = None
    enrichment_failed: bool = False


@dataclass
class PreparedMediaImport:
    index: int
    input_title_id: str
    target: dict
    alist_path: str
    probe: dict
    bundle: ImportContext


class _PersistenceError(MediaImportError):
    def __init__(self, prepared: PreparedMediaImport | None, error: Exception):
        super().__init__(str(error))
        self.index = prepared.index if prepared else 0
        self.title_id = prepared.input_title_id if prepared else ""


def _known_douban_id(library_db: sqlite3.Connection, title_id: str) -> str | None:
    return get_douban_id(library_db, title_id)


def _observed_rating(service, function, *args):
    with events.activity("rating", service, service) as outcome:
        result = function(*args)
        status = (result or {}).get("status", "success")
        if status not in ("success", "no_key", "not_found", "skipped", "unavailable"):
            outcome["failed"] = True
            code = {"http_429": "rate_limit", "http_503": "busy"}.get(status, "error")
            events.emit("issue", stage="rating", service=service, code=code,
                        reason=events.safe_text((result or {}).get("message")) or "评分获取失败")
        return result


def _build_context(library_db: sqlite3.Connection, root_title_id: str, scope=None) -> ImportContext:
    catalog = load_title_catalog(library_db, root_title_id)
    events.emit("title", name=catalog["root"].get("display_title") or root_title_id)
    events.emit("plan", stage="rating", total=2)
    rating_args = (_observed_rating, "MDBList", fetch_title_rating, root_title_id)
    rating_future = scope.submit("rating", root_title_id, *rating_args) if scope else runtime.pool("rating").submit(root_title_id, *rating_args)
    # 豆瓣的低速接口使用独立有界队列，不占用 TMDB 元数据或 MDBList 评分工作线程。
    submit = scope.submit if scope else lambda name, key, fn, *args: runtime.pool(name).submit(key, fn, *args)
    douban_future = submit("douban", root_title_id, _observed_rating, "豆瓣", fetch_douban_rating,
                           root_title_id, catalog["root"]["title_type"], _known_douban_id(library_db, root_title_id))

    metadata = catalog["metadata"]
    root = catalog["root"]
    titles = [root]
    relations = []
    artwork = list(metadata.get("title_artwork") or [])
    for item in metadata.get("episodes") or []:
        episode = item["title_basic"]
        titles.append(episode)
        relations.append(
            {
                "episode_id": episode["title_id"],
                "series_id": root["title_id"],
                "season_number": item["season_number"],
                "episode_number": item["episode_number"],
            }
        )
        artwork.extend(item.get("title_artwork") or [])

    return ImportContext(
        root=root,
        titles=titles,
        relations=relations,
        artwork=artwork,
        cast=list(metadata.get("title_cast") or []),
        rating_result={"status": "pending"},
        rating_future=rating_future,
        douban_future=douban_future,
        episodes_by_position={
            (episode["season_number"], episode["episode_number"]): episode
            for episode in catalog["episodes"]
        },
        episodes_by_id={episode["title_id"]: episode for episode in catalog["episodes"]},
    )


def _resolve_target_from_context(
    context: ImportContext,
    title_id: str,
    season_value: str,
    episode_value: str,
) -> dict:
    if title_id == context.root["title_id"]:
        if context.root["title_type"] == "movie":
            if season_value.strip() or episode_value.strip():
                raise MediaImportError("Movie Title ID 不需要填写季号和集号。")
            return context.root
        season_number, episode_number = _parse_position(season_value, episode_value)
        target = context.episodes_by_position.get((season_number, episode_number))
        if target is None:
            raise MediaImportError(
                f"TMDB 中没有 S{season_number:02d} E{episode_number:02d}，或该单集没有 IMDb ID。"
            )
        return target

    if title_id in context.episodes_by_id:
        target = context.episodes_by_id[title_id]
        _validate_episode_position(season_value, episode_value, target)
        return target

    raise MediaImportError("指定的 Title ID 不属于当前剧集上下文。")


def _validate_title_id(title_id: str) -> str:
    title_id = title_id.strip()
    if TITLE_ID_PATTERN.fullmatch(title_id) is None:
        raise MediaImportError("Title ID 格式无效，应类似 tt0816692。")
    return title_id


def _check_file(
    library_db: sqlite3.Connection,
    target_title_id: str,
    source: str,
    seen_alist_paths: set[str] | None = None,
) -> str:
    """本地校验：解析来源、批内去重、是否已关联其他作品。只读库，不联网。"""
    alist_path = media_source.parse_source(source)
    if Path(alist_path).suffix.lower() not in media_source.VIDEO_EXTENSIONS:
        raise MediaImportError("该文件不是支持的视频格式。")

    if seen_alist_paths is not None:
        if alist_path in seen_alist_paths:
            raise MediaImportError("同一批次不能重复填写同一个媒体文件。")
        seen_alist_paths.add(alist_path)

    existing = get_file_targets(library_db, [alist_path]).get(alist_path)
    if existing is not None and existing["title_id"] != target_title_id:
        raise MediaImportError("该媒体文件已关联其他影视条目。")
    return alist_path


def _probe_file(source: str, alist_path: str, raw_url: str = "") -> dict:
    if not raw_url:
        raw_url = media_source.get_file(alist_path)["raw_url"]
    return media_probe.probe_video(raw_url)


def _stage_context_images(
    context: ImportContext,
    artwork: list[dict],
    image_root: Path,
    staging_root: Path,
) -> None:
    """下载并暂存作品图片与演员照（只联网与写临时目录，可在线程中执行）。"""
    derivatives, derived_files = [], []

    def derive(row, source):
        with events.activity("derive", row["local_path"], "生成预览"):
            derive_image(row, source)

    def derive_image(row, source):
        from PIL import Image
        local_path = row["local_path"]
        size = get_target_size_for_path(local_path)
        with Image.open(source) as image:
            image.draft("RGB", size or (128, 128))
            image.load()
            if size:
                target = staging_root / ".derived" / local_path
                if generate_thumbnail(source, target, size, source_image=image):
                    derived_files.append((target, image_root.parent / "thumbnails" / local_path))
                else:
                    context.enrichment_failed = True
            if wants_tone(local_path):
                tone = compute_tone(source, source_image=image)
                if tone:
                    context.tone_data[local_path] = {**tone, "mtime": source.stat().st_mtime_ns}
                else:
                    context.enrichment_failed = True

    def downloaded(row, source):
        if get_target_size_for_path(row["local_path"]) or wants_tone(row["local_path"]):
            derivatives.append(runtime.pool("derive").submit(context.root["title_id"], derive, row, source))

    expected = len(artwork)
    profiles = sum(bool(person.get("local_path")) for person in context.cast)
    events.emit("plan", stage="image", total=expected + profiles)
    try:
        context.artwork, artwork_files = _stage_new_artwork(
            artwork, image_root, staging_root, on_download=downloaded)
        context.cast, cast_files = _stage_cast_images(context.cast, image_root, staging_root)
    finally:
        wait(derivatives)
    context.enrichment_failed |= len(context.artwork) < expected or sum(bool(person.get("local_path")) for person in context.cast) < profiles
    for future in derivatives:
        try:
            future.result()
        except Exception as error:
            context.enrichment_failed = True
            logger.warning("Image derivative preparation failed: %s", type(error).__name__)
    context.staged_files = artwork_files + cast_files + derived_files
    context.images_prepared = True


def _prepare_items(
    library_db: sqlite3.Connection,
    image_root: Path,
    staging_root: Path,
    items: list[dict],
    progress: dict,
    on_progress=None,
    raw_urls: dict[str, str] | None = None,
    link_resolver=None,
    probe_cache=None,
    on_probe=None,
    defer_enrichment=False,
    scope=None,
) -> list[PreparedMediaImport]:
    """Prepare independent roots in shared pools, keeping SQLite readers local.

    Ready roots immediately release file probes and artwork/derivative work.
    Probes acquire links just in time, share the global and per-source caps, and
    emit per-file progress. Legacy imports join images here; durable jobs commit
    core associations first and join optional work in the enrichment phase.
    Any failed core preparation cancels queued work and preserves input ordering.
    """
    seen_alist_paths: set[str] = set()
    submit = scope.submit if scope else lambda name, key, fn, *args, **kwargs: runtime.pool(name).submit(key, fn, *args, **kwargs)
    inputs, contexts, identities = [], {}, {}
    # Reject malformed local input before asking any provider for metadata.
    for index, item in enumerate(items, start=1):
        progress.update(index=index, title_id="")
        title_id = _validate_title_id(item.get("title_id") or "")
        progress["title_id"] = title_id
        alist_url = item.get("path") or item.get("alist_url", "")
        alist_path = media_source.parse_source(alist_url)
        if alist_path in seen_alist_paths:
            raise MediaImportError("同一批次不能重复填写同一个媒体文件。")
        seen_alist_paths.add(alist_path)
        inputs.append((index, title_id, item, alist_url, alist_path))
        if title_id not in identities:
            identities[title_id] = submit("metadata", title_id, tmdb.identify_title, title_id)

    context_futures = {}
    image_futures, probe_futures = {}, {}
    database_path = library_db.execute("PRAGMA database_list").fetchone()[2]

    def build(root_id):
        with closing(connect_database(database_path)) as db:
            context = _build_context(db, root_id, scope)
            artwork = _new_artwork(db, context.artwork, image_root)
        return context, artwork

    for index, title_id, item, _, _ in inputs:
        progress.update(index=index, title_id=title_id)
        identity = identities[title_id].result()
        if identity["title_type"] == "series":
            _parse_position(item.get("season_number", ""), item.get("episode_number", ""))
        root_id = identity["root_id"]
        if root_id not in context_futures:
            context_futures[root_id] = submit("metadata", root_id, build, root_id)

    # Process a root as soon as it is ready; a slow movie cannot delay the
    # probes/images of other movies. All SQLite readers belong to their thread.
    planned = []
    root_ids = {future: root_id for root_id, future in context_futures.items()}
    progress_lock = threading.Lock()
    linked = 0

    def probe(index, title_id, alist_url, alist_path):
        item = inputs[index - 1][2]
        season, episode = item.get("season_number"), item.get("episode_number")
        label = f"S{int(season):02}E{int(episode):02}" if season not in (None, "") and episode not in (None, "") else Path(alist_path).name
        with events.activity("probe", index, label):
            return probe_file(index, title_id, alist_url, alist_path)

    events.emit("plan", stage="probe", total=len(inputs))

    def probe_file(index, title_id, alist_url, alist_path):
        nonlocal linked
        cached = (probe_cache or {}).get(alist_path)
        raw = (raw_urls or {}).get(alist_path, "")
        if link_resolver is not None:
            link = link_resolver(alist_path)
            alist_url, raw = link["signed_url"], link.get("raw_url", "")
            with progress_lock:
                linked += 1
                if on_progress:
                    on_progress("link", linked, len(inputs))
        if cached is not None:
            return cached
        try:
            result = _probe_file(alist_url, alist_path, raw)
        except media_probe.ProbeError as error:
            if error.code != "expired" or link_resolver is None:
                raise
            link = link_resolver(alist_path)
            result = _probe_file(link["signed_url"], alist_path, link.get("raw_url", ""))
        if on_probe:
            on_probe(alist_path, result)
        return result

    try:
        for future in as_completed(root_ids):
            root_id = root_ids[future]
            first = next(row for row in inputs if identities[row[1]].result()["root_id"] == root_id)
            progress.update(index=first[0], title_id=first[1])
            context, artwork = future.result()
            contexts[root_id] = context
            targets = set()
            for index, title_id, item, alist_url, alist_path in inputs:
                if identities[title_id].result()["root_id"] != root_id:
                    continue
                progress.update(index=index, title_id=title_id)
                target = _resolve_target_from_context(context, title_id, item.get("season_number", ""), item.get("episode_number", ""))
                _check_file(library_db, target["title_id"], alist_url)
                targets.add(target["title_id"])
                planned.append((index, title_id, target, alist_url, alist_path, context))
                source = alist_path.strip("/").split("/", 1)[0]
                limit = runtime.setting("FFPROBE_PER_SOURCE", throttle.ffprobe_workers(), 8)
                probe_future = submit("probe", root_id, probe, index, title_id, alist_url, alist_path, _resource=(source, limit))
                probe_futures[probe_future] = (index, title_id)
            artwork = [row for row in artwork if row["title_id"] == root_id or row["title_id"] in targets]
            image_future = submit("artwork", root_id, _stage_context_images, context, artwork, image_root, staging_root)
            context.image_future = image_future
            image_futures[image_future] = (first[0], first[1])
    except BaseException:
        for future in (*probe_futures, *image_futures):
            future.cancel()
        wait((*probe_futures, *image_futures))
        raise

    total = len(planned)
    probes: dict[int, dict] = {}
    failures: list[tuple[int, str, Exception]] = []
    try:
        for future in as_completed(probe_futures):
            index, title_id = probe_futures[future]
            error = future.exception()
            if error is not None:
                failures.append((index, title_id, error))
                for other in (*probe_futures, *image_futures):
                    other.cancel()
                break
            probes[index] = future.result()
            if on_progress:
                on_progress("probe", len(probes), total)
    finally:
        wait((*probe_futures, *(image_futures if failures or not defer_enrichment else ())))
    # Gather every running failure after cancellation, rather than claiming the
    # first completion is necessarily the lowest input index.
    observed = {**probe_futures, **({} if defer_enrichment and not failures else image_futures)}
    for future, (index, title_id) in observed.items():
        if not future.cancelled() and future.done() and future.exception() is not None:
            failures.append((index, title_id, future.exception()))
    if failures:
        # 并行时以序号最小的失败为准，报错位置稳定可复现。
        index, title_id, error = min(failures, key=lambda failure: failure[0])
        progress.update(index=index, title_id=title_id)
        raise error

    return [
        PreparedMediaImport(
            index=index,
            input_title_id=title_id,
            target=target,
            alist_path=alist_path,
            probe=probes[index],
            bundle=context,
        )
        for index, title_id, target, _, alist_path, context in sorted(planned, key=lambda item: item[0])
    ]


def _write_prepared_import(
    db: sqlite3.Connection,
    prepared: PreparedMediaImport,
    written_root_ids: set[str],
    published_files: list[Path],
    enrichment=True,
) -> int:
    bundle = prepared.bundle
    root_title_id = bundle.root["title_id"]
    write_bundle = root_title_id not in written_root_ids
    if write_bundle and enrichment:
        _publish_staged_files(bundle.staged_files, published_files)

    file_id = import_media_bundle(
        titles=bundle.titles if write_bundle else [],
        relations=bundle.relations if write_bundle else [],
        artwork=bundle.artwork if write_bundle and enrichment else [],
        cast=bundle.cast if write_bundle and enrichment else None,
        target_title_id=prepared.target["title_id"],
        alist_path=prepared.alist_path,
        probe=prepared.probe,
        # MDBList 成功时带全部平台评分，否则只有豆瓣的字段（可能为空）
        ratings_by_title=(
            {root_title_id: _ratings_to_write(bundle.rating_result)}
            if write_bundle and enrichment and _ratings_to_write(bundle.rating_result)
            else None
        ),
        db=db,
    )
    written_root_ids.add(root_title_id)
    return file_id


def _ratings_to_write(rating_result: dict) -> dict:
    ratings = rating_result.get("ratings") or {}
    if rating_result.get("status") == "success":
        return ratings
    return {key: value for key, value in ratings.items() if key.startswith("douban_")}


def _result(prepared: PreparedMediaImport, file_id: int) -> dict:
    bundle = prepared.bundle
    return {
        "file_id": file_id,
        "title_id": prepared.target["title_id"],
        "display_title": prepared.target["display_title"],
        "root_title_id": bundle.root["title_id"],
        "root_display_title": bundle.root["display_title"],
        "title_count": len(bundle.titles),
        "artwork_count": len(bundle.artwork),
        "rating_status": bundle.rating_result.get("status", "skipped"),
    }


def _apply_imdb_ratings(prepared_items: list[PreparedMediaImport]) -> dict | None:
    """从 IMDb 评分文件填入 average_rating / num_votes；不可用时保留库内旧值。"""
    titles = {
        title["title_id"]: title
        for prepared in prepared_items
        for title in prepared.bundle.titles
    }
    try:
        ratings = imdb_ratings.lookup_ratings(titles)
    except (OSError, ValueError, EOFError) as error:
        logger.warning("IMDb 评分文件读取失败：%s", error)
        return {"status": "error", "message": "IMDb 评分文件读取失败"}
    for title_id, (rating, votes) in ratings.items():
        titles[title_id]["average_rating"] = rating
        titles[title_id]["num_votes"] = votes


def _collect_ratings(bundle: ImportContext) -> None:
    if bundle.rating_future is not None:
        try:
            bundle.rating_result = bundle.rating_future.result()
        except Exception:
            bundle.rating_result = {"status": "error"}
    if bundle.douban_future is not None:
        try:
            fields = bundle.douban_future.result()
        except Exception as error:
            logger.info("豆瓣评分获取失败（%s）：%s", bundle.root["title_id"], type(error).__name__)
            fields = {}
        bundle.rating_result = {**bundle.rating_result,
            "ratings": {**(bundle.rating_result.get("ratings") or {}), **fields}}


def _persist_prepared_imports(
    database_path: Path,
    image_root: Path,
    prepared_items: list[PreparedMediaImport],
    *, enrichment=True, commit_guard=None,
) -> list[dict]:
    for bundle in ({id(item.bundle): item.bundle for item in prepared_items}.values() if enrichment else ()):
        _collect_ratings(bundle)
    if enrichment:
        _apply_imdb_ratings(prepared_items)
    published_files = []
    written_root_ids = set()
    current = None
    with runtime.write_lock:
        try:
            if commit_guard:
                commit_guard()
            with closing(connect_writable(database_path)) as db, db:
                results = []
                for current in prepared_items:
                    file_id = _write_prepared_import(db, current, written_root_ids, published_files, enrichment=enrichment)
                    results.append(_result(current, file_id))
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            _remove_moved_files(published_files)
            raise _PersistenceError(current, error) from error

    if not enrichment:
        return results
    _finish_derivatives(image_root, prepared_items)
    return results


def _finish_derivatives(image_root, prepared_items):
    thumbnail_paths = {
        image["local_path"]
        for prepared in prepared_items
        for image in prepared.bundle.artwork
    }
    def build_thumbnail(local_path: str) -> None:
        try:
            with events.activity("derive", local_path, "生成预览"):
                ensure_thumbnail(local_path, static_root=image_root.parent)
        except Exception as error:
            logger.warning("Failed to build thumbnail for %s: %s", local_path, error)

    futures = [runtime.pool("derive").submit(str(image_root), build_thumbnail, path) for path in sorted(thumbnail_paths)]
    for future in futures:
        future.result()
    try:
        merge_tones({path: tone for item in prepared_items for path, tone in item.bundle.tone_data.items()}, image_root.parent)
        ensure_tones(thumbnail_paths, static_root=image_root.parent)
    except Exception as error:
        logger.warning("Failed to build image tones: %s", error)


def _enrich_prepared(database_path, image_root, prepared_items, commit_guard=None):
    bundles = {id(item.bundle): item.bundle for item in prepared_items}.values()
    # Start the local rating lookup while remote images and scores are running.
    events.emit("plan", stage="rating", total=1)
    imdb_future = runtime.pool("imdb").submit(str(database_path), _observed_rating, "IMDb", _apply_imdb_ratings, prepared_items)
    retry, warning = 0, False
    for bundle in bundles:
        try:
            bundle.image_future.result()
            _collect_ratings(bundle)
            imdb_future.result()
            published = []
            with runtime.write_lock:
                if commit_guard:
                    commit_guard()
                try:
                    with closing(connect_writable(database_path)) as db, db:
                        _publish_staged_files(bundle.staged_files, published)
                        if not enrich_media_bundle(db, bundle.root["title_id"], artwork=bundle.artwork, cast=bundle.cast,
                            ratings=_ratings_to_write(bundle.rating_result),
                            imdb=[(title["average_rating"], title["num_votes"], title["title_id"]) for title in bundle.titles if title.get("average_rating") is not None]):
                            _remove_moved_files(published)
                            continue
                except Exception:
                    _remove_moved_files(published)
                    raise
            status = bundle.rating_result.get("status", "skipped")
            warning |= bundle.enrichment_failed or status not in ("success", "no_key", "not_found", "skipped", "unavailable")
            if bundle.enrichment_failed or status in ("error", "http_429") or status.startswith("http_5"):
                retry = max(retry, bundle.rating_result.get("retry_after") or 0,
                            throttle.limiter("tmdb-image").blocked_for(), throttle.limiter("mdblist").blocked_for(), 5)
        except Exception as error:
            logger.warning("Optional import data failed for %s: %s", bundle.root["title_id"], type(error).__name__)
            retry = max(retry, 5)
            warning = True
    _finish_derivatives(image_root, prepared_items)
    return retry, warning


IMPORT_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    sqlite3.Error,
    requests.RequestException,
)


def _run_import(library_database, image_root, items: list[dict], progress: dict, on_progress=None,
                raw_urls: dict[str, str] | None = None, *, link_resolver=None, probe_cache=None, on_probe=None,
                defer_enrichment=False, commit_guard=None) -> list[dict]:
    library_database = Path(library_database)
    image_root = Path(image_root)
    image_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".media-import-",
        dir=image_root.parent,
    ) as temporary_directory, closing(
        connect_database(library_database)
    ) as library_db, runtime.Scope() as scope:
        prepared_items = _prepare_items(
            library_db,
            image_root,
            Path(temporary_directory),
            items,
            progress,
            on_progress,
            raw_urls,
            link_resolver=link_resolver, probe_cache=probe_cache, on_probe=on_probe,
            defer_enrichment=defer_enrichment,
            scope=scope,
        )
        if on_progress:
            on_progress("write", 0, len(items))
        try:
            results = _persist_prepared_imports(library_database, image_root, prepared_items,
                                               enrichment=not defer_enrichment, commit_guard=commit_guard)
            if defer_enrichment:
                if on_progress:
                    on_progress("enrich", len(items), len(items))
                retry, warning = _enrich_prepared(library_database, image_root, prepared_items, commit_guard)
                for result in results:
                    result["enrichment_retry_after"] = retry
                    result["enrichment_warning"] = warning
        finally:
            wait([item.bundle.image_future for item in prepared_items if item.bundle.image_future])
    return [{"index": prepared.index, **result} for prepared, result in zip(prepared_items, results)]


def normalize_batch_import_items(payload) -> list[dict]:
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise MediaImportError("JSON 顶层必须是数组，或包含 items 数组。")
    if not items:
        raise MediaImportError("批量录入至少需要一条数据。")
    if len(items) > MAX_BATCH_ITEMS:
        raise MediaImportError(f"单次最多录入 {MAX_BATCH_ITEMS} 条数据。")

    normalized = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise MediaImportError(f"第 {index} 条数据必须是 JSON 对象。")
        normalized.append(
            {
                field: _normalize_batch_value(item.get(field), index, field)
                for field in (
                    "title_id",
                    "season_number",
                    "episode_number",
                    "path",
                    "alist_url",
                )
            }
        )
    return normalized


def _normalize_batch_value(value, index: int, field: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MediaImportError(f"第 {index} 条的 {field} 必须是字符串、整数或 null。")
    return str(value).strip()


def import_media_batch(
    *,
    library_database: str | Path,
    image_root: str | Path,
    items: list[dict],
    on_progress=None,
    raw_urls: dict[str, str] | None = None,
    link_resolver=None,
    probe_cache=None,
    on_probe=None,
    defer_enrichment=False,
    commit_guard=None,
) -> dict:
    """raw_urls 只供服务端内部传入（刚从 media_source 取得的 {alist_path: raw_url}），不接受请求数据。"""
    progress = {"index": 0, "title_id": ""}
    try:
        successes = _run_import(library_database, image_root, items, progress, on_progress, raw_urls,
                                link_resolver=link_resolver, probe_cache=probe_cache, on_probe=on_probe,
                                defer_enrichment=defer_enrichment, commit_guard=commit_guard)
    except _PersistenceError as error:
        events.emit("issue", stage="write", **events.error_info(error))
        failure = {"index": error.index, "title_id": error.title_id, "message": str(error)}
    except IMPORT_ERRORS as error:
        events.emit("issue", **events.error_info(error))
        failure = {**progress, "message": str(error)}
        if isinstance(error, (requests.ConnectionError, requests.Timeout, throttle.RateLimitError)) or getattr(error, "retryable", False):
            failure.update(retryable=True, retry_after=getattr(error, "retry_after", None) or 5)
    else:
        return {"successes": successes, "failures": []}
    return {"successes": [], "failures": [failure]}
