from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import requests

from connectors import alist, imdb_ratings, media_probe, tmdb
from models.database import connect_database, connect_writable
from models.library import import_media_bundle
from queries.association import get_series_episodes
from services.rating_sync import fetch_title_rating
from services.image_tones import ensure_tones
from services.thumbnails import ensure_thumbnail, normalize_image_path


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


def _new_artwork(db: sqlite3.Connection, artwork: list[dict]) -> list[dict]:
    if not artwork:
        return []

    title_ids = list(dict.fromkeys(image["title_id"] for image in artwork))
    placeholders = ", ".join("?" for _ in title_ids)
    existing = {
        tuple(row)
        for row in db.execute(
            f"""
            SELECT title_id, artwork_type, position
              FROM title_artwork
             WHERE title_id IN ({placeholders})
            """,
            tuple(title_ids),
        )
    }
    return [
        image
        for image in artwork
        if (
            image["title_id"],
            image["artwork_type"],
            image["position"],
        )
        not in existing
    ]


def _artwork_path(root: Path, local_path: str) -> Path:
    path = normalize_image_path(local_path)
    if path is None:
        raise MediaImportError(f"图片路径无效：{local_path}")
    return root.joinpath(*path.parts[1:])


def _stage_new_artwork(
    artwork: list[dict],
    image_root: Path,
    staging_root: Path,
) -> tuple[list[dict], list[tuple[Path, Path]]]:
    if not artwork:
        return [], []

    pending = []
    successful = []
    staged_files = []
    for image in artwork:
        target = _artwork_path(image_root, image["local_path"])
        if target.is_file():
            successful.append(image)
        else:
            pending.append(image)

    if pending:
        tmdb.download_metadata_images(
            {"title_artwork": pending, "episodes": []},
            staging_root,
        )
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


def _build_context(library_db: sqlite3.Connection, root_title_id: str) -> ImportContext:
    with ThreadPoolExecutor(max_workers=1) as executor:
        rating_future = executor.submit(fetch_title_rating, root_title_id)
        catalog = load_title_catalog(library_db, root_title_id)
        rating_result = rating_future.result()

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
        rating_result=rating_result,
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


def _resolve_item(
    library_db: sqlite3.Connection,
    contexts: dict[str, ImportContext],
    title_id: str,
    season_value: str,
    episode_value: str,
) -> tuple[ImportContext, dict]:
    """解析一条录入：同一作品（含其单集 ID）复用已构建的上下文。"""
    context = contexts.get(title_id)
    if context is None:
        identity = tmdb.identify_title(title_id)
        # 先校验季集号，避免无效输入触发整部作品的元数据请求。
        if identity["title_type"] == "series":
            _parse_position(season_value, episode_value)
        elif identity["title_type"] == "movie" and (season_value.strip() or episode_value.strip()):
            raise MediaImportError("Movie Title ID 不需要填写季号和集号。")
        root_id = identity["root_id"]
        context = contexts.get(root_id)
        if context is None:
            context = _build_context(library_db, root_id)
            contexts[root_id] = context
            for episode_id in context.episodes_by_id:
                contexts.setdefault(episode_id, context)
        if identity["title_type"] == "episode" and title_id not in context.episodes_by_id:
            raise MediaImportError("TMDB 中该单集缺少季集信息，无法录入。")
    return context, _resolve_target_from_context(context, title_id, season_value, episode_value)


def _validate_title_id(title_id: str) -> str:
    title_id = title_id.strip()
    if TITLE_ID_PATTERN.fullmatch(title_id) is None:
        raise MediaImportError("Title ID 格式无效，应类似 tt0816692。")
    return title_id


def _prepare_file(
    library_db: sqlite3.Connection,
    target_title_id: str,
    alist_url: str,
    seen_alist_paths: set[str] | None = None,
) -> tuple[str, dict]:
    alist_path = alist.parse_alist_url(alist_url)

    if seen_alist_paths is not None:
        if alist_path in seen_alist_paths:
            raise MediaImportError("同一批次不能重复填写同一个 AList 文件。")
        seen_alist_paths.add(alist_path)

    existing = library_db.execute(
        "SELECT title_id FROM media_file WHERE alist_path = ?",
        (alist_path,),
    ).fetchone()
    if existing is not None and existing["title_id"] != target_title_id:
        raise MediaImportError("该 AList 文件已关联其他影视条目。")

    alist.validate_alist_video_url(alist_url)
    video_url = alist.get_video_link(alist_path)
    return alist_path, media_probe.probe_video(video_url)


def _prepare_context_images(
    context: ImportContext,
    library_db: sqlite3.Connection,
    image_root: Path,
    staging_root: Path,
) -> None:
    if context.images_prepared:
        return
    artwork = _new_artwork(library_db, context.artwork)
    context.artwork, artwork_files = _stage_new_artwork(
        artwork,
        image_root,
        staging_root,
    )
    context.cast, cast_files = _stage_cast_images(
        context.cast,
        image_root,
        staging_root,
    )
    context.staged_files = artwork_files + cast_files
    context.images_prepared = True


def _prepare_items(
    library_db: sqlite3.Connection,
    image_root: Path,
    staging_root: Path,
    items: list[dict],
    progress: dict,
    on_progress=None,
) -> list[PreparedMediaImport]:
    """联网解析、探测并暂存图片；全部完成后才进入写事务。progress 记录当前条目供报错。

    on_progress(phase, done, total) 在每个条目准备完成后调用，供界面显示真实进度。
    """
    contexts: dict[str, ImportContext] = {}
    seen_alist_paths: set[str] = set()
    prepared_items = []
    for index, item in enumerate(items, start=1):
        progress.update(index=index, title_id="")
        title_id = _validate_title_id(item.get("title_id") or "")
        progress["title_id"] = title_id
        context, target = _resolve_item(
            library_db,
            contexts,
            title_id,
            item.get("season_number", ""),
            item.get("episode_number", ""),
        )
        alist_path, probe = _prepare_file(
            library_db,
            target["title_id"],
            item.get("alist_url", ""),
            seen_alist_paths,
        )
        _prepare_context_images(context, library_db, image_root, staging_root)
        prepared_items.append(
            PreparedMediaImport(
                index=index,
                input_title_id=title_id,
                target=target,
                alist_path=alist_path,
                probe=probe,
                bundle=context,
            )
        )
        if on_progress:
            on_progress("probe", index, len(items))
    return prepared_items


def _write_prepared_import(
    db: sqlite3.Connection,
    prepared: PreparedMediaImport,
    written_root_ids: set[str],
    published_files: list[Path],
) -> int:
    bundle = prepared.bundle
    root_title_id = bundle.root["title_id"]
    write_bundle = root_title_id not in written_root_ids
    if write_bundle:
        _publish_staged_files(bundle.staged_files, published_files)

    file_id = import_media_bundle(
        titles=bundle.titles if write_bundle else [],
        relations=bundle.relations if write_bundle else [],
        artwork=bundle.artwork if write_bundle else [],
        cast=bundle.cast if write_bundle else None,
        target_title_id=prepared.target["title_id"],
        alist_path=prepared.alist_path,
        probe=prepared.probe,
        ratings_by_title=(
            {root_title_id: bundle.rating_result["ratings"]}
            if write_bundle
            and bundle.rating_result.get("status") == "success"
            else None
        ),
        db=db,
    )
    written_root_ids.add(root_title_id)
    return file_id


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


def _apply_imdb_ratings(prepared_items: list[PreparedMediaImport]) -> None:
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
        return
    for title_id, (rating, votes) in ratings.items():
        titles[title_id]["average_rating"] = rating
        titles[title_id]["num_votes"] = votes


def _persist_prepared_imports(
    database_path: Path,
    image_root: Path,
    prepared_items: list[PreparedMediaImport],
) -> list[dict]:
    _apply_imdb_ratings(prepared_items)
    published_files = []
    written_root_ids = set()
    current = None
    try:
        with closing(connect_writable(database_path)) as db, db:
            results = []
            for current in prepared_items:
                file_id = _write_prepared_import(
                    db,
                    current,
                    written_root_ids,
                    published_files,
                )
                results.append(_result(current, file_id))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        _remove_moved_files(published_files)
        raise _PersistenceError(current, error) from error

    thumbnail_paths = {
        image["local_path"]
        for prepared in prepared_items
        for image in prepared.bundle.artwork
    }
    for local_path in thumbnail_paths:
        try:
            ensure_thumbnail(local_path, static_root=image_root.parent)
        except Exception as error:
            logger.warning("Failed to build thumbnail for %s: %s", local_path, error)
    try:
        ensure_tones(thumbnail_paths, static_root=image_root.parent)
    except Exception as error:
        logger.warning("Failed to build image tones: %s", error)
    return results


IMPORT_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    sqlite3.Error,
    requests.RequestException,
)


def _run_import(library_database, image_root, items: list[dict], progress: dict, on_progress=None) -> list[dict]:
    library_database = Path(library_database)
    image_root = Path(image_root)
    image_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".media-import-",
        dir=image_root.parent,
    ) as temporary_directory, closing(
        connect_database(library_database)
    ) as library_db:
        prepared_items = _prepare_items(
            library_db,
            image_root,
            Path(temporary_directory),
            items,
            progress,
            on_progress,
        )
        if on_progress:
            on_progress("write", 0, len(items))
        results = _persist_prepared_imports(library_database, image_root, prepared_items)
    return [{"index": prepared.index, **result} for prepared, result in zip(prepared_items, results)]


def import_media(
    *,
    library_database: str | Path,
    image_root: str | Path,
    title_id: str,
    season_number: str = "",
    episode_number: str = "",
    alist_url: str,
) -> dict:
    item = {
        "title_id": title_id,
        "season_number": season_number,
        "episode_number": episode_number,
        "alist_url": alist_url,
    }
    try:
        result = _run_import(library_database, image_root, [item], {})[0]
    except MediaImportError:
        raise
    except IMPORT_ERRORS as error:
        raise MediaImportError(str(error)) from error
    result.pop("index")
    return result


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
) -> dict:
    progress = {"index": 0, "title_id": ""}
    try:
        successes = _run_import(library_database, image_root, items, progress, on_progress)
    except _PersistenceError as error:
        failure = {"index": error.index, "title_id": error.title_id, "message": str(error)}
    except IMPORT_ERRORS as error:
        failure = {**progress, "message": str(error)}
    else:
        return {"successes": successes, "failures": []}
    return {"successes": [], "failures": [failure]}
