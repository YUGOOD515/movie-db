"""IMDb official ratings, with an on-disk index rebuilt once per source version."""

import gzip
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from time import time

import requests

from services import paths

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
RATINGS_PATH = Path(
    os.environ.get("IMDB_RATINGS_PATH") or paths.DATA_DIR / "imdb" / "title.ratings.tsv.gz"
)
# IMDb 每天更新一次；超过一天再下载，失败时沿用旧文件。
MAX_AGE_SECONDS = 24 * 3600
REQUEST_TIMEOUT = 60

logger = logging.getLogger(__name__)
_lock = threading.RLock()


def ensure_ratings_file(path: Path = RATINGS_PATH, max_age: float = MAX_AGE_SECONDS) -> Path | None:
    with _lock:
        return _ensure_ratings_file(path, max_age)


def _ensure_ratings_file(path: Path, max_age: float) -> Path | None:
    """返回可用的评分文件路径；过期时重新下载，下载失败且无旧文件时返回 None。"""
    if path.is_file() and time() - path.stat().st_mtime < max_age:
        return path
    temporary = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(RATINGS_URL, stream=True, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            with temporary.open("wb") as file:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    file.write(chunk)
        with gzip.open(temporary, "rt", encoding="utf-8") as file:
            if not file.readline().startswith("tconst\taverageRating\tnumVotes"):
                raise ValueError("IMDb 评分文件格式无法识别")
        temporary.replace(path)
    except (OSError, ValueError, requests.RequestException) as error:
        temporary.unlink(missing_ok=True)
        logger.warning("IMDb 评分文件下载失败：%s", error)
    return path if path.is_file() else None


def lookup_ratings(title_ids, path: Path | None = None) -> dict[str, tuple[float, int]]:
    """Use bounded memory; concurrent movie jobs share one refresh/index build."""
    wanted = set(title_ids)
    if not wanted:
        return {}
    with _lock:
        path = path or ensure_ratings_file()
        if path is None:
            return {}
        index = _ensure_index(Path(path))
    ratings, ids = {}, list(wanted)
    with closing(sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True)) as db:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for title_id, rating, votes in db.execute(f"SELECT title_id, rating, votes FROM rating WHERE title_id IN ({','.join('?' for _ in chunk)})", chunk):
                ratings[title_id] = (rating, votes)
    return ratings


def _ensure_index(path):
    stat = path.stat()
    signature = f"{stat.st_size}:{stat.st_mtime_ns}"
    index = path.with_name(path.name + ".sqlite3")
    if index.is_file():
        try:
            with closing(sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True)) as db:
                if db.execute("SELECT signature FROM source").fetchone() == (signature,):
                    return index
        except sqlite3.Error:
            pass
    temporary = index.with_name(f".{index.name}.{uuid.uuid4().hex}.tmp")
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.executescript("CREATE TABLE source (signature TEXT); CREATE TABLE rating (title_id TEXT PRIMARY KEY, rating REAL, votes INTEGER) WITHOUT ROWID;")
            db.execute("INSERT INTO source VALUES (?)", (signature,))
            with gzip.open(path, "rt", encoding="utf-8") as file:
                if not next(file, "").startswith("tconst\taverageRating\tnumVotes"):
                    raise ValueError("IMDb 评分文件格式无法识别")
                batch = []
                for line in file:
                    title_id, rating, votes = line.rstrip("\n").split("\t")
                    batch.append((title_id, float(rating), int(votes)))
                    if len(batch) >= 10000:
                        db.executemany("INSERT INTO rating VALUES (?, ?, ?)", batch)
                        batch.clear()
                db.executemany("INSERT INTO rating VALUES (?, ?, ?)", batch)
        temporary.replace(index)
    finally:
        temporary.unlink(missing_ok=True)
    return index
