from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import requests

from connectors import alist, media_probe, tmdb
from models.database import connect_database, connect_imdb
from models.library import import_media_bundle
from queries.imdb import (
    get_episode_by_position,
    get_episode_relation,
    get_imdb_title,
    get_series_episodes,
)


TITLE_ID_PATTERN = re.compile(r"tt[0-9]+")
MAX_BATCH_ITEMS = 100


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


def _build_bundle(imdb_db, root: dict, metadata: dict) -> tuple[list, list, list]:
    titles = [_library_title(root, metadata.get("title_basic"))]
    relations = []
    artwork = list(metadata.get("title_artwork") or [])

    if root["title_type"] != "series":
        return titles, relations, artwork

    metadata_by_position = {
        (item.get("season_number"), item.get("episode_number")): item
        for item in metadata.get("episodes") or []
    }
    for episode in get_series_episodes(imdb_db, root["title_id"]):
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
    return titles, relations, artwork


def _new_artwork(database_path: Path, artwork: list[dict]) -> list[dict]:
    if not artwork:
        return []
    with closing(connect_database(database_path)) as db:
        existing = {
            tuple(row)
            for row in db.execute(
                "SELECT title_id, artwork_type, position FROM title_artwork"
            ).fetchall()
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


def _download_new_artwork(
    artwork: list[dict],
    image_root: Path,
) -> tuple[list[dict], list[Path]]:
    if not artwork:
        return [], []

    metadata = {"title_artwork": artwork, "episodes": []}
    image_root.parent.mkdir(parents=True, exist_ok=True)
    moved = []
    successful = []
    with tempfile.TemporaryDirectory(
        prefix=".media-import-", dir=image_root.parent
    ) as temporary_directory:
        staging_root = Path(temporary_directory)
        tmdb.download_metadata_images(metadata, staging_root)
        try:
            for image in artwork:
                relative = Path(image["local_path"])
                source = staging_root / Path(*relative.parts[1:])
                if not source.is_file():
                    continue
                target = image_root / Path(*relative.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    successful.append(image)
                    continue
                shutil.move(str(source), str(target))
                moved.append(target)
                successful.append(image)
        except Exception:
            _remove_moved_files(moved)
            raise
    return successful, moved


def _remove_moved_files(paths: list[Path]) -> None:
    for path in reversed(paths):
        path.unlink(missing_ok=True)


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
    title_id = title_id.strip()
    if TITLE_ID_PATTERN.fullmatch(title_id) is None:
        raise MediaImportError("Title ID 格式无效，应类似 tt0816692。")

    library_database = Path(library_database)
    image_root = Path(image_root)
    moved_files = []
    try:
        with closing(connect_imdb(imdb_database)) as imdb_db:
            resolved = _resolve_target(
                imdb_db,
                title_id,
                season_number,
                episode_number,
            )
            alist_path = alist.parse_alist_url(alist_url)
            alist.validate_alist_video_url(alist_url)

            with closing(connect_database(library_database)) as library_db:
                existing = library_db.execute(
                    "SELECT title_id FROM media_file WHERE alist_path = ?",
                    (alist_path,),
                ).fetchone()
            if existing is not None and existing["title_id"] != resolved["target"]["title_id"]:
                raise MediaImportError("该 AList 文件已关联其他影视条目。")

            video_url = alist.get_video_link(alist_path)
            probe = media_probe.probe_video(video_url)
            metadata = tmdb.build_media_metadata(resolved["root"]["title_id"])
            titles, relations, artwork = _build_bundle(
                imdb_db,
                resolved["root"],
                metadata,
            )

        artwork = _new_artwork(library_database, artwork)
        artwork, moved_files = _download_new_artwork(artwork, image_root)
        file_id = import_media_bundle(
            library_database,
            titles=titles,
            relations=relations,
            artwork=artwork,
            target_title_id=resolved["target"]["title_id"],
            alist_path=alist_path,
            probe=probe,
        )
        return {
            "file_id": file_id,
            "title_id": resolved["target"]["title_id"],
            "display_title": resolved["target"]["display_title"],
            "root_title_id": resolved["root"]["title_id"],
            "root_display_title": resolved["root"]["display_title"],
            "title_count": len(titles),
            "artwork_count": len(artwork),
        }
    except MediaImportError:
        _remove_moved_files(moved_files)
        raise
    except (OSError, RuntimeError, ValueError, sqlite3.Error, requests.RequestException) as error:
        _remove_moved_files(moved_files)
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
    successes = []
    failures = []
    for index, item in enumerate(items, start=1):
        try:
            result = import_media(
                library_database=library_database,
                imdb_database=imdb_database,
                image_root=image_root,
                **item,
            )
        except MediaImportError as error:
            failures.append(
                {
                    "index": index,
                    "title_id": item.get("title_id", ""),
                    "message": str(error),
                }
            )
        else:
            successes.append({"index": index, **result})
    return {"successes": successes, "failures": failures}
