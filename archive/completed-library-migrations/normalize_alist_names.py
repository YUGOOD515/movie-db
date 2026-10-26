"""递归读取 AList 视频并输出 GuessIt 标准化名称。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from functools import partial
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

import requests
from guessit import guessit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from connectors import alist
from models.database import connect_database


VIDEO_EXTENSIONS = frozenset(
    {".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv"}
)
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
LIBRARY_DATABASE = PROJECT_ROOT / "library.db"
EPISODE_PREFIX = re.compile(r"^(?:S\d{1,3}E\d{1,4}|\d{1,3}x\d{1,4})", re.IGNORECASE)
SEASON_DIRECTORY = re.compile(r"^(?:season\s*\d+|第\s*\d+\s*季)", re.IGNORECASE)


def normalize_root(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if any(part in {".", ".."} for part in value.split("/")):
        raise argparse.ArgumentTypeError("AList 路径不能包含 . 或 ..")
    return "/" + value.strip("/")


def clean_name(value: object) -> str:
    return re.sub(r"\s+", " ", INVALID_FILENAME_CHARS.sub(" ", str(value))).strip(" .-")


def child_path(parent: str, item: dict) -> str:
    return str(item.get("virtual_path") or PurePosixPath(parent) / item["name"])


def iter_video_paths(
    path: str,
    list_directory: Callable[[str], list[dict]],
) -> Iterator[str]:
    for item in sorted(list_directory(path), key=lambda value: value["name"].casefold()):
        current = child_path(path, item)
        if item["is_dir"]:
            yield from iter_video_paths(current, list_directory)
        elif PurePosixPath(current).suffix.casefold() in VIDEO_EXTENSIONS:
            yield current


def _numbers(value: object) -> tuple[int, ...]:
    return tuple(int(number) for number in value) if isinstance(value, list) else (int(value),)


def _episode_code(season: object, episode: object) -> str:
    season_number = _numbers(season)[0]
    episode_numbers = _numbers(episode)
    return f"S{season_number:02d}E{episode_numbers[0]:02d}" + "".join(
        f"-E{number:02d}" for number in episode_numbers[1:]
    )


def guess_media(source: PurePosixPath) -> dict:
    parsed = dict(guessit(source.name))
    if parsed.get("type") != "episode" or EPISODE_PREFIX.match(source.stem) is None:
        return parsed

    episode_title = parsed.get("episode_title") or parsed.get("title")
    parent = next(
        (value.name for value in source.parents if value.name and not SEASON_DIRECTORY.match(value.name)),
        "",
    )
    parent_title = guessit(parent).get("title")
    if parent_title:
        parsed["title"] = parent_title
        parsed["episode_title"] = episode_title
    return parsed


def load_library_media(database_path: Path) -> dict[str, dict]:
    with closing(connect_database(database_path)) as database:
        rows = database.execute(
            """
            SELECT media.file_id,
                   media.alist_path,
                   title.title_id,
                   title.title_type,
                   title.primary_title,
                   title.display_title,
                   title.start_year,
                   relation.series_id,
                   relation.season_number,
                   relation.episode_number,
                   series.primary_title AS series_title,
                   series.display_title AS series_display_title
              FROM media_file AS media
              JOIN title_basic AS title
                ON title.title_id = media.title_id
              LEFT JOIN title_relation AS relation
                ON relation.episode_id = title.title_id
              LEFT JOIN title_basic AS series
                ON series.title_id = relation.series_id
            """
        )
        return {row["alist_path"]: dict(row) for row in rows}


def normalize_library_media(path: str, media: dict) -> dict:
    source = PurePosixPath(path)
    is_episode = media["title_type"] == "episode"
    title = clean_name(
        (media["series_title"] or media["series_display_title"])
        if is_episode
        else (media["primary_title"] or media["display_title"])
    )
    episode_title = clean_name(media["primary_title"]) if is_episode else None
    if is_episode:
        code = _episode_code(media["season_number"], media["episode_number"])
        standard_name = f"{title} {code} - {episode_title}"
    else:
        standard_name = f"{title} ({media['start_year']})" if media["start_year"] else title

    return {
        "path": path,
        "source": "library",
        "file_id": media["file_id"],
        "title_id": media["title_id"],
        "series_id": media["series_id"],
        "type": media["title_type"],
        "title": title,
        "display_title": media["series_display_title"] if is_episode else media["display_title"],
        "episode_title": episode_title,
        "episode_display_title": media["display_title"] if is_episode else None,
        "year": media["start_year"],
        "season": media["season_number"],
        "episode": media["episode_number"],
        "standard_name": standard_name,
        "standard_filename": standard_name + source.suffix.casefold(),
    }


def normalize_media(path: str, library_media: dict[str, dict] | None = None) -> dict:
    if library_media and path in library_media:
        return normalize_library_media(path, library_media[path])

    source = PurePosixPath(path)
    parsed = guess_media(source)
    title = clean_name(parsed.get("title") or source.stem)
    if part := parsed.get("part"):
        title = f"{title} Part {part}"

    is_episode = parsed.get("season") is not None and parsed.get("episode") is not None
    is_movie = parsed.get("year") is not None and parsed.get("season") is None and parsed.get("episode") is None
    if is_episode:
        code = _episode_code(parsed["season"], parsed["episode"])
        episode_title = clean_name(parsed.get("episode_title") or "")
        standard_name = f"{title} {code}" + (f" - {episode_title}" if episode_title else "")
    elif is_movie:
        year = parsed.get("year")
        standard_name = f"{title} ({year})" if year else title
        episode_title = None
    else:
        title = source.stem
        standard_name = source.stem
        episode_title = None

    return {
        "path": path,
        "source": "guessit" if is_episode or is_movie else "unknown",
        "file_id": None,
        "title_id": None,
        "series_id": None,
        "type": "episode" if is_episode else "movie" if is_movie else "unknown",
        "title": title,
        "display_title": None,
        "episode_title": episode_title,
        "episode_display_title": None,
        "year": parsed.get("year") if is_episode or is_movie else None,
        "season": parsed.get("season") if is_episode else None,
        "episode": parsed.get("episode") if is_episode else None,
        "standard_name": standard_name,
        "standard_filename": standard_name + (source.suffix.casefold() if is_episode or is_movie else source.suffix),
    }


def scan_alist(
    paths: list[str],
    list_directory: Callable[[str], list[dict]],
    library_media: dict[str, dict] | None = None,
) -> list[dict]:
    videos = chain.from_iterable(iter_video_paths(path, list_directory) for path in paths)
    return list(map(partial(normalize_media, library_media=library_media), videos))


def list_with_progress(path: str, token: str) -> list[dict]:
    print(f"正在扫描：{path}", file=sys.stderr, flush=True)
    return alist.list_directory(path, token)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["/"], type=normalize_root, help="AList 目录；默认 /，可指定多个")
    parser.add_argument("--output", type=Path, help="写入 UTF-8 JSON；默认输出到终端")
    parser.add_argument("--database", type=Path, default=LIBRARY_DATABASE, help="只读 library.db 路径")
    args = parser.parse_args()

    username = os.environ.get("ALIST_USERNAME", "").strip()
    password = os.environ.get("ALIST_PASSWORD", "")
    if not username or not password:
        parser.error("请设置 ALIST_USERNAME 和 ALIST_PASSWORD")

    try:
        token = alist.get_token(username, password)
        library_media = load_library_media(args.database)
        records = scan_alist(args.paths, partial(list_with_progress, token=token), library_media)
    except (KeyError, RuntimeError, sqlite3.Error, ValueError, requests.RequestException) as error:
        parser.exit(1, f"AList 读取失败：{error}\n")

    output = json.dumps(records, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
