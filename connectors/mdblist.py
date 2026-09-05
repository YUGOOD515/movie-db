"""MDBList rating configuration, normalization and requests."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Any, Final, Mapping, Optional, Sequence

import requests

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
ENV_PATH: Final[Path] = PROJECT_ROOT / ".env"

# ==============================================================================
# 不可变数据模型 (Immutable Data Models)
# ==============================================================================

@dataclass(frozen=True)
class Config:
    mdblist_apikey: str
    timeout_seconds: float = 8.0

@dataclass(frozen=True)
class PlatformRating:
    platform_key: str          # 平台键 (如 tomatoes_critic, metacritic_critic 等)
    platform_name: str         # 显示名称 (如 "Rotten Tomatoes (影评人)")
    score_display: str         # 显示字符串 (如 "94%", "8.7/10", "4.5/5.0")
    score_10: Optional[float]  # 归一化 10 分制
    score_100: Optional[float] # 归一化 100 分制
    votes: Optional[int]       # 评价人数 / 投票数
    url: Optional[str] = None  # 外部源链接

@dataclass(frozen=True)
class TitleRatingsSummary:
    imdb_id: str
    status: str                # 'success', 'not_found', 'error', 'no_key'
    latency_ms: float
    title: Optional[str]
    year: Optional[int]
    media_type: Optional[str]  # 'movie', 'show', etc.
    score: Optional[int]       # MDBList 加权总分 (0-100)
    score_average: Optional[int] # MDBList 平均总分 (0-100)
    platform_ratings: tuple[PlatformRating, ...] = field(default_factory=tuple)
    error_message: Optional[str] = None

# ==============================================================================
# 纯函数：环境配置读取 (Pure Config Loader)
# ==============================================================================

def load_env_mapping(filepath: Path = ENV_PATH) -> Mapping[str, str]:
    """纯函数：解析 .env 文件的键值对。"""
    if not filepath.is_file():
        return {}
    lines = filepath.read_text(encoding="utf-8").splitlines()
    pairs = [
        line.strip().split("=", 1)
        for line in lines
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]
    return {k.strip(): v.strip() for k, v in pairs}

def create_config(overrides: Optional[Mapping[str, Any]] = None) -> Config:
    """纯函数：构造全局只读配置对象。"""
    env = {**load_env_mapping(), **os.environ}
    if overrides:
        env.update(overrides)
    return Config(
        mdblist_apikey=env.get("MDBLIST_APIKEY", ""),
        timeout_seconds=float(env.get("HTTP_TIMEOUT", "8.0")),
    )

# ==============================================================================
# 纯函数：评分解析与归一化 (Rating Parsers & Normalizers)
# ==============================================================================

def round_safe(value: Optional[float], decimals: int = 1) -> Optional[float]:
    """安全浮点数舍入。"""
    return round(value, decimals) if value is not None else None

def normalize_to_scales(
    score: Optional[float | int], max_score: float = 10.0
) -> tuple[Optional[float], Optional[float]]:
    """将任意量纲分值纯函数转换归一化为 (10分制, 100分制)。"""
    if score is None or max_score <= 0:
        return None, None
    score_10 = round_safe((score / max_score) * 10.0, 1)
    score_100 = round_safe((score / max_score) * 100.0, 1)
    return score_10, score_100

def create_rating_item(
    key: str,
    name: str,
    raw_value: Optional[float | int],
    max_value: float,
    display_fmt: str,
    votes: Optional[int] = None,
    url: Optional[str] = None,
) -> Optional[PlatformRating]:
    """纯函数：构造单个平台的评分条目不可变对象。"""
    if raw_value is None or (raw_value == 0 and votes is None):
        return None
    s10, s100 = normalize_to_scales(float(raw_value), max_value)
    display_str = display_fmt.format(val=raw_value, max=max_value, s10=s10, s100=s100)
    return PlatformRating(
        platform_key=key,
        platform_name=name,
        score_display=display_str,
        score_10=s10,
        score_100=s100,
        votes=votes,
        url=url,
    )

def parse_mdblist_payload(imdb_id: str, data: Mapping[str, Any], latency_ms: float) -> TitleRatingsSummary:
    """纯函数：从 MDBList API 响应中提取结构化评分摘要。"""
    raw_ratings = data.get("ratings")
    if not raw_ratings:
        return TitleRatingsSummary(
            imdb_id=imdb_id,
            status="not_found",
            latency_ms=round(latency_ms, 2),
            title=data.get("title"),
            year=data.get("year"),
            media_type=data.get("type"),
            score=data.get("score"),
            score_average=data.get("score_average"),
            error_message="No ratings returned for this title ID (e.g. single episode or unindexed)",
        )

    rating_map: dict[str, Mapping[str, Any]] = {
        r.get("source"): r for r in raw_ratings if isinstance(r, dict) and r.get("source")
    }

    # 平台解析规则表 (纯声明式映射定义)
    rules: Sequence[tuple[str, str, str, float, str, Optional[str]]] = [
        # (source_key, platform_key, platform_name, max_val, format_str, url_prefix)
        ("imdb", "imdb", "IMDb", 10.0, "{val:.1f}/10", "https://www.imdb.com/title/"),
        ("tomatoes", "tomatoes_critic", "Rotten Tomatoes (影评人)", 100.0, "{val:.0f}%", "https://www.rottentomatoes.com"),
        ("tomatoesaudience", "tomatoes_audience", "Rotten Tomatoes (观众)", 100.0, "{val:.0f}%", None),
        ("metacritic", "metacritic_critic", "Metacritic (Metascore)", 100.0, "{val:.0f}/100", "https://www.metacritic.com"),
        ("metacriticuser", "metacritic_user", "Metacritic (用户)", 10.0, "{val:.1f}/10", None),
        ("tmdb", "tmdb", "TMDb", 100.0, "{val:.0f}%", None),
        ("trakt", "trakt", "Trakt", 100.0, "{val:.0f}%", None),
        ("letterboxd", "letterboxd", "Letterboxd", 5.0, "{val:.1f}/5.0", "https://letterboxd.com"),
        ("rogerebert", "rogerebert", "Roger Ebert", 4.0, "{val:.1f}/4.0 stars", "https://www.rogerebert.com/reviews/"),
        ("myanimelist", "myanimelist", "MyAnimeList", 10.0, "{val:.2f}/10", "https://myanimelist.net/anime/"),
    ]

    ratings: list[PlatformRating] = []
    for src_key, p_key, p_name, max_v, fmt, prefix in rules:
        item = rating_map.get(src_key)
        if not item or item.get("value") is None:
            continue
        val = item.get("value")
        if float(val) == 0 and item.get("votes") is None:
            continue
        item_url = item.get("url")
        if src_key == "imdb" and not item_url:
            full_url = f"https://www.imdb.com/title/{imdb_id}/"
        elif prefix and item_url:
            full_url = f"{prefix}{item_url}"
        else:
            full_url = item_url
        r = create_rating_item(
            key=p_key,
            name=p_name,
            raw_value=val,
            max_value=max_v,
            display_fmt=fmt,
            votes=item.get("votes"),
            url=full_url,
        )
        if r:
            ratings.append(r)

    return TitleRatingsSummary(
        imdb_id=imdb_id,
        status="success",
        latency_ms=round(latency_ms, 2),
        title=data.get("title"),
        year=data.get("year"),
        media_type=data.get("type"),
        score=data.get("score"),
        score_average=data.get("score_average"),
        platform_ratings=tuple(ratings),
    )

# ==============================================================================
# 纯函数：核心调用入口 (Core Fetch Pipeline)
# ==============================================================================

def fetch_mdblist_ratings(
    imdb_id: str, config: Optional[Config] = None
) -> TitleRatingsSummary:
    """
    通过 MDBList 获取多平台影视评分的主入口纯函数。

    >>> summary = fetch_mdblist_ratings("tt0816692")
    >>> print(summary.title, summary.score)
    """
    cfg = config or create_config()
    if not cfg.mdblist_apikey:
        return TitleRatingsSummary(
            imdb_id=imdb_id,
            status="no_key",
            latency_ms=0.0,
            title=None,
            year=None,
            media_type=None,
            score=None,
            score_average=None,
            error_message="MDBLIST_APIKEY is not configured in environment or .env",
        )

    url = f"https://mdblist.com/api/?apikey={cfg.mdblist_apikey}&i={imdb_id}"
    t0 = time.perf_counter()
    try:
        resp = requests.get(url, timeout=cfg.timeout_seconds)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            return TitleRatingsSummary(
                imdb_id=imdb_id,
                status=f"http_{resp.status_code}",
                latency_ms=round(elapsed_ms, 2),
                title=None,
                year=None,
                media_type=None,
                score=None,
                score_average=None,
                error_message=resp.text[:120],
            )
        data = resp.json()
        return parse_mdblist_payload(imdb_id, data, elapsed_ms)
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return TitleRatingsSummary(
            imdb_id=imdb_id,
            status="error",
            latency_ms=round(elapsed_ms, 2),
            title=None,
            year=None,
            media_type=None,
            score=None,
            score_average=None,
            error_message=str(e),
        )

fetch_title_ratings = fetch_mdblist_ratings
