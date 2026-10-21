"""IMDb 官方评分文件 title.ratings.tsv.gz：按需下载、流式查询，不建本地大库。"""

import gzip
import logging
import os
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


def ensure_ratings_file(path: Path = RATINGS_PATH, max_age: float = MAX_AGE_SECONDS) -> Path | None:
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
    """流式扫描评分文件，只返回指定 Title ID 的 (评分, 票数)。"""
    wanted = set(title_ids)
    if not wanted:
        return {}
    path = path or ensure_ratings_file()
    if path is None:
        return {}
    ratings = {}
    with gzip.open(path, "rt", encoding="utf-8") as file:
        next(file, None)
        for line in file:
            title_id, _, rest = line.partition("\t")
            if title_id in wanted:
                rating, _, votes = rest.rstrip("\n").partition("\t")
                ratings[title_id] = (float(rating), int(votes))
                if len(ratings) == len(wanted):
                    break
    return ratings
