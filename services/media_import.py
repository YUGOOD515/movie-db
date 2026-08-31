from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import requests

from connectors import alist, media_probe, tmdb
from models.database import connect_database, connect_imdb, connect_writable
from models.library import import_media_bundle
from queries.imdb import (
    get_episode_by_position,
    get_episode_relation,
    get_imdb_title,
    get_series_episodes,
)
from services.rating_sync import fetch_title_rating
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


def _resolve_target(
    imdb_db,
    title_id: str,
    season_value: str,
    episode_value: str,
) -> dict:
    title = get_imdb_title(imdb_db, title_id)
    if title is None or not title["title_type"]:
        raise MediaImportError("本地 IMDb 数据库中不存在可录入的 Title ID。")

    if title["title_type"] == "movie":
        if season_value.strip() or episode_value.strip():
            raise MediaImportError("Movie Title ID 不需要填写季号和集号。")
        return {"root": title, "target": title, "relation": None}

    if title["title_type"] == "series":
        season_number, episode_number = _parse_position(
            season_value, episode_value
        )
        relation = get_episode_by_position(
            imdb_db,
            title_id,
            season_number,
            episode_number,
        )
        if relation is None:
            raise MediaImportError("本地 IMDb 数据库中不存在指定的剧集单集。")
        target = get_imdb_title(imdb_db, relation["episode_id"])
        return {"root": title, "target": target, "relation": relation}

    if title["title_type"] == "episode":
        relation = get_episode_relation(imdb_db, title_id)
        if relation is None:
            raise MediaImportError("本地 IMDb 数据库中缺少该单集的父剧集关系。")
        if season_value.strip() or episode_value.strip():
            expected = (relation["season_number"], relation["episode_number"])
            actual = _parse_position(season_value, episode_value)
            if actual != expected:
                raise MediaImportError("填写的季集号与 Episode Title ID 不一致。")
        root = get_imdb_title(imdb_db, relation["series_id"])
        if root is None or root["title_type"] != "series":
            raise MediaImportError("本地 IMDb 数据库中缺少该单集的父剧集。")
        return {"root": root, "target": title, "relation": relation}

    raise MediaImportError("仅支持 movie、series 和 episode 类型的 Title ID。")


def _library_title(imdb_title: dict, supplement: dict | None = None) -> dict:
    supplement = supplement or {}
    return {
        "title_id": imdb_title["title_id"],
        "tmdb_id": supplement.get("tmdb_id"),
        "title_type": imdb_title["title_type"],
        "primary_title": imdb_title.get("primary_title"),
        "original_title": imdb_title.get("original_title"),
        "original_language": supplement.get("original_language"),
        "display_title": imdb_title["display_title"],
        "is_adult": int(imdb_title.get("is_adult") or 0),
        "start_year": imdb_title.get("start_year"),
        "end_year": imdb_title.get("end_year"),
        "runtime_minutes": imdb_title.get("runtime_minutes"),
        "genres": imdb_title.get("genres"),
        "overview": supplement.get("overview"),
        "tagline": supplement.get("tagline"),
        "average_rating": imdb_title.get("average_rating"),
        "num_votes": imdb_title.get("num_votes"),
    }


def _build_bundle(imdb_db, root: dict, metadata: dict) -> tuple[list, list, list, list]:
    titles = [_library_title(root, metadata.get("title_basic"))]
    relations = []
    artwork = list(metadata.get("title_artwork") or [])
    episodes_list = []

    if root["title_type"] != "series":
        return titles, relations, artwork, episodes_list

    metadata_by_position = {
        (item.get("season_number"), item.get("episode_number")): item
        for item in metadata.get("episodes") or []
    }
    episodes_list = get_series_episodes(imdb_db, root["title_id"])
    for episode in episodes_list:
        position = (episode["season_number"], episode["episode_number"])
        episode_metadata = metadata_by_position.get(position) or {}
        titles.append(_library_title(episode, episode_metadata.get("title_basic")))
        relations.append(
            {
                "episode_id": episode["title_id"],
                "series_id": root["title_id"],
                "season_number": episode["season_number"],
                "episode_number": episode["episode_number"],
            }
        )
        for image in episode_metadata.get("title_artwork") or []:
            if image.get("title_id") == episode["title_id"]:
                artwork.append(image)
    return titles, relations, artwork, episodes_list


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
class ImportBundle:
    root: dict
    titles: list[dict]
    relations: list[dict]
    artwork: list[dict]
    rating_result: dict
    staged_files: list[tuple[Path, Path]]


@dataclass
class SeriesImportContext(ImportBundle):
    episodes_by_position: dict[tuple[int, int], dict]
    episodes_by_id: dict[str, dict]
    artwork_prepared: bool = False


@dataclass
class PreparedMediaImport:
    index: int
    input_title_id: str
    target: dict
    alist_path: str
    probe: dict
    bundle: ImportBundle


class _PersistenceError(MediaImportError):
    def __init__(self, prepared: PreparedMediaImport | None, error: Exception):
        super().__init__(str(error))
        self.index = prepared.index if prepared else 0
        self.title_id = prepared.input_title_id if prepared else ""


def _build_series_context(imdb_db, root_title: dict) -> SeriesImportContext:
    root_title_id = root_title["title_id"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        rating_future = executor.submit(fetch_title_rating, root_title_id)
        metadata = tmdb.build_media_metadata(root_title_id)
        titles, relations, artwork, episodes_list = _build_bundle(
            imdb_db, root_title, metadata
        )
        rating_result = rating_future.result()

    episodes_by_position = {}
    episodes_by_id = {}
    for episode in episodes_list:
        pos = (episode["season_number"], episode["episode_number"])
        episodes_by_position[pos] = episode
        episodes_by_id[episode["title_id"]] = episode

    return SeriesImportContext(
        root=root_title,
        titles=titles,
        relations=relations,
        artwork=artwork,
        rating_result=rating_result,
        staged_files=[],
        episodes_by_position=episodes_by_position,
        episodes_by_id=episodes_by_id,
    )


def _resolve_target_from_context(
    context: SeriesImportContext,
    title_id: str,
    season_value: str,
    episode_value: str,
) -> dict:
    if title_id == context.root["title_id"]:
        season_number, episode_number = _parse_position(season_value, episode_value)
        target = context.episodes_by_position.get((season_number, episode_number))
        if target is None:
            raise MediaImportError("本地 IMDb 数据库中不存在指定的剧集单集。")
        return {"root": context.root, "target": target, "relation": None}

    if title_id in context.episodes_by_id:
        target = context.episodes_by_id[title_id]
        if season_value.strip() or episode_value.strip():
            expected = (target["season_number"], target["episode_number"])
            actual = _parse_position(season_value, episode_value)
            if actual != expected:
                raise MediaImportError("填写的季集号与 Episode Title ID 不一致。")
        return {"root": context.root, "target": target, "relation": None}

    raise MediaImportError("指定的 Title ID 不属于当前剧集上下文。")


def _validate_title_id(title_id: str) -> str:
    title_id = title_id.strip()
    if TITLE_ID_PATTERN.fullmatch(title_id) is None:
        raise MediaImportError("Title ID 格式无效，应类似 tt0816692。")
    return title_id


def _prepare_file(
    library_db: sqlite3.Connection,
    resolved: dict,
    alist_url: str,
    seen_alist_paths: set[str] | None = None,
) -> tuple[str, dict]:
    alist_path = alist.parse_alist_url(alist_url)
    target_title_id = resolved["target"]["title_id"]

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


def _prepare_series_artwork(
    context: SeriesImportContext,
    library_db: sqlite3.Connection,
    image_root: Path,
    staging_root: Path,
) -> None:
    if context.artwork_prepared:
        return
    artwork = _new_artwork(library_db, context.artwork)
    context.artwork, context.staged_files = _stage_new_artwork(
        artwork,
        image_root,
        staging_root,
    )
    context.artwork_prepared = True


def _prepare_import(
    *,
    index: int,
    title_id: str,
    resolved: dict,
    library_db: sqlite3.Connection,
    imdb_db: sqlite3.Connection,
    image_root: Path,
    staging_root: Path,
    alist_url: str,
    seen_alist_paths: set[str] | None = None,
    series_context: SeriesImportContext | None = None,
) -> PreparedMediaImport:
    alist_path, probe = _prepare_file(
        library_db,
        resolved,
        alist_url,
        seen_alist_paths,
    )

    if series_context is not None:
        _prepare_series_artwork(
            series_context,
            library_db,
            image_root,
            staging_root,
        )
        return PreparedMediaImport(
            index=index,
            input_title_id=title_id,
            target=resolved["target"],
            alist_path=alist_path,
            probe=probe,
            bundle=series_context,
        )

    root_title_id = resolved["root"]["title_id"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        rating_future = executor.submit(fetch_title_rating, root_title_id)
        metadata = tmdb.build_media_metadata(root_title_id)
        titles, relations, artwork, _ = _build_bundle(
            imdb_db,
            resolved["root"],
            metadata,
        )
        rating_result = rating_future.result()

    artwork = _new_artwork(library_db, artwork)
    artwork, staged_files = _stage_new_artwork(
        artwork,
        image_root,
        staging_root,
    )
    return PreparedMediaImport(
        index=index,
        input_title_id=title_id,
        target=resolved["target"],
        alist_path=alist_path,
        probe=probe,
        bundle=ImportBundle(
            root=resolved["root"],
            titles=titles,
            relations=relations,
            artwork=artwork,
            rating_result=rating_result,
            staged_files=staged_files,
        ),
    )


def _write_prepared_import(
    db: sqlite3.Connection,
    prepared: PreparedMediaImport,
    written_series_ids: set[str],
    published_files: list[Path],
) -> int:
    bundle = prepared.bundle
    is_shared_series = isinstance(bundle, SeriesImportContext)
    root_title_id = bundle.root["title_id"]
    write_bundle = (
        not is_shared_series or root_title_id not in written_series_ids
    )
    if write_bundle:
        _publish_staged_files(bundle.staged_files, published_files)

    file_id = import_media_bundle(
        titles=bundle.titles if write_bundle else [],
        relations=bundle.relations if write_bundle else [],
        artwork=bundle.artwork if write_bundle else [],
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
    if is_shared_series:
        written_series_ids.add(root_title_id)
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


def _persist_prepared_imports(
    database_path: Path,
    image_root: Path,
    prepared_items: list[PreparedMediaImport],
) -> list[dict]:
    published_files = []
    written_series_ids = set()
    current = None
    try:
        with closing(connect_writable(database_path)) as db, db:
            results = []
            for current in prepared_items:
                file_id = _write_prepared_import(
                    db,
                    current,
                    written_series_ids,
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
    return results


def import_media(
    *,
    library_database: str | Path,
    imdb_database: str | Path,
    image_root: str | Path,
    title_id: str,
    season_number: str = "",
    episode_number: str = "",
    alist_url: str,
) -> dict:
    title_id = _validate_title_id(title_id)

    library_database = Path(library_database)
    image_root = Path(image_root)
    image_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix=".media-import-",
            dir=image_root.parent,
        ) as temporary_directory, closing(
            connect_imdb(imdb_database)
        ) as imdb_db, closing(
            connect_database(library_database)
        ) as library_db:
            staging_root = Path(temporary_directory)
            resolved = _resolve_target(
                imdb_db,
                title_id,
                season_number,
                episode_number,
            )
            context = None
            if resolved["root"]["title_type"] == "series":
                context = _build_series_context(imdb_db, resolved["root"])
                resolved = _resolve_target_from_context(
                    context,
                    title_id,
                    season_number,
                    episode_number,
                )
            prepared = _prepare_import(
                index=1,
                title_id=title_id,
                resolved=resolved,
                library_db=library_db,
                imdb_db=imdb_db,
                image_root=image_root,
                staging_root=staging_root,
                alist_url=alist_url,
                series_context=context,
            )
            return _persist_prepared_imports(
                library_database,
                image_root,
                [prepared],
            )[0]
    except MediaImportError:
        raise
    except (
        OSError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
        requests.RequestException,
    ) as error:
        raise MediaImportError(str(error)) from error


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
    imdb_database: str | Path,
    image_root: str | Path,
    items: list[dict],
) -> dict:
    library_database = Path(library_database)
    image_root = Path(image_root)
    image_root.parent.mkdir(parents=True, exist_ok=True)
    prepared_items = []
    contexts_by_title: dict[str, SeriesImportContext] = {}
    seen_alist_paths: set[str] = set()
    current_index = 0
    current_title_id = ""

    try:
        with tempfile.TemporaryDirectory(
            prefix=".media-import-",
            dir=image_root.parent,
        ) as temporary_directory, closing(
            connect_imdb(imdb_database)
        ) as imdb_db, closing(
            connect_database(library_database)
        ) as library_db:
            staging_root = Path(temporary_directory)
            for current_index, item in enumerate(items, start=1):
                current_title_id = _validate_title_id(item.get("title_id") or "")
                context = contexts_by_title.get(current_title_id)
                if context is not None:
                    resolved = _resolve_target_from_context(
                        context,
                        current_title_id,
                        item.get("season_number", ""),
                        item.get("episode_number", ""),
                    )
                else:
                    resolved = _resolve_target(
                        imdb_db,
                        current_title_id,
                        item.get("season_number", ""),
                        item.get("episode_number", ""),
                    )
                    if resolved["root"]["title_type"] == "series":
                        root_id = resolved["root"]["title_id"]
                        context = contexts_by_title.get(root_id)
                        if context is None:
                            context = _build_series_context(
                                imdb_db,
                                resolved["root"],
                            )
                            contexts_by_title[root_id] = context
                            for episode_id in context.episodes_by_id:
                                contexts_by_title[episode_id] = context
                        resolved = _resolve_target_from_context(
                            context,
                            current_title_id,
                            item.get("season_number", ""),
                            item.get("episode_number", ""),
                        )

                prepared_items.append(
                    _prepare_import(
                        index=current_index,
                        title_id=current_title_id,
                        resolved=resolved,
                        library_db=library_db,
                        imdb_db=imdb_db,
                        image_root=image_root,
                        staging_root=staging_root,
                        alist_url=item.get("alist_url", ""),
                        seen_alist_paths=seen_alist_paths,
                        series_context=context,
                    )
                )

            results = _persist_prepared_imports(
                library_database,
                image_root,
                prepared_items,
            )
    except _PersistenceError as error:
        return {
            "successes": [],
            "failures": [
                {
                    "index": error.index,
                    "title_id": error.title_id,
                    "message": str(error),
                }
            ],
        }
    except MediaImportError as error:
        return {
            "successes": [],
            "failures": [
                {
                    "index": current_index,
                    "title_id": current_title_id,
                    "message": str(error),
                }
            ],
        }
    except (
        OSError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
        requests.RequestException,
    ) as error:
        return {
            "successes": [],
            "failures": [
                {
                    "index": current_index,
                    "title_id": current_title_id,
                    "message": str(error),
                }
            ],
        }

    return {
        "successes": [
            {"index": prepared.index, **result}
            for prepared, result in zip(prepared_items, results)
        ],
        "failures": [],
    }
