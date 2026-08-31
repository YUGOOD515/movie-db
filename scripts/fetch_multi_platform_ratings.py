"""通过 IMDb ID 获取多平台影视评分的函数式脚本（仅保留 MDBList）。

MDBList 聚合返回的平台评分包括：
1. IMDb 官方评分 & 投票总人数 & 流行度排名
2. Rotten Tomatoes (烂番茄) - 影评人专业评分 (Tomatometer) & 评论数
3. Rotten Tomatoes (烂番茄) - 爆米花大众评分 (Popcornmeter / Audience) & 投票数
4. Metacritic - 媒体专家综合评分 (Metascore) & 评测数
5. Metacritic - 用户评分 (User Score) & 评论数
6. TMDb (The Movie Database) - 评分 & 投票数
7. Trakt - 影视打分 & 投票数
8. Letterboxd - 影迷评分 (5分制) & 观影人次
9. Roger Ebert - 权威影评人星级评分 (4星制)
10. MyAnimeList - 动画番剧评分与投票数（若适用）
11. MDBList 综合加权总评分 (Score) & 综合平均分 (Score Average)

用法：
    # 查询单个电影
    uv run python scripts/fetch_multi_platform_ratings.py tt0816692

    # 查询剧集
    uv run python scripts/fetch_multi_platform_ratings.py tt11280740

    # 输出 JSON 格式
    uv run python scripts/fetch_multi_platform_ratings.py tt0816692 --json

    # 从 library.db 抽样测试并生成完整对比报告
    uv run python scripts/fetch_multi_platform_ratings.py --benchmark
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Callable, Dict, Final, List, Mapping, Optional, Sequence, Tuple
import requests

# 确保在 Windows 终端中正确输出 UTF-8 字符
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
LIBRARY_DB_PATH: Final[Path] = PROJECT_ROOT / "library.db"
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
    platform_ratings: Tuple[PlatformRating, ...] = field(default_factory=tuple)
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
) -> Tuple[Optional[float], Optional[float]]:
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

    rating_map: Dict[str, Mapping[str, Any]] = {
        r.get("source"): r for r in raw_ratings if isinstance(r, dict) and r.get("source")
    }

    # 平台解析规则表 (纯声明式映射定义)
    rules: Sequence[Tuple[str, str, str, float, str, Optional[str]]] = [
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

    ratings: List[PlatformRating] = []
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

# ==============================================================================
# 格式化输出与基准测试 (Reporting & Benchmark)
# ==============================================================================

def format_summary_text(summary: TitleRatingsSummary) -> str:
    """纯函数：将评分摘要格式化为终端易读的表格文本。"""
    lines = []
    lines.append("=" * 66)
    title_str = summary.title or "Unknown Title"
    year_str = f" ({summary.year})" if summary.year else ""
    type_str = f"[{summary.media_type.upper()}] " if summary.media_type else ""
    lines.append(f"{type_str}{title_str}{year_str} - {summary.imdb_id}")
    lines.append(f"MDBList 调用耗时: {summary.latency_ms} ms | 接口状态: {summary.status}")
    if summary.score is not None or summary.score_average is not None:
        score_str = f"综合评分: {summary.score}/100" if summary.score is not None else ""
        avg_str = f"平均分: {summary.score_average}/100" if summary.score_average is not None else ""
        lines.append(f"MDBList 综合指数: {score_str} | {avg_str}".strip(" |"))
    lines.append("-" * 66)
    lines.append(f"{'平台':<28} | {'评分':<14} | {'10分换算':<8} | {'评价人数'}")
    lines.append("-" * 66)

    if not summary.platform_ratings:
        lines.append("  (未找到该条目的聚合评分数据)")
        if summary.error_message:
            lines.append(f"  提示: {summary.error_message}")
    else:
        for r in summary.platform_ratings:
            votes_str = f"{r.votes:,}" if r.votes is not None else "--"
            s10_str = f"{r.score_10:.1f}" if r.score_10 is not None else "--"
            lines.append(
                f"{r.platform_name:<28} | {r.score_display:<14} | {s10_str:<8} | {votes_str}"
            )

    lines.append("=" * 66)
    return "\n".join(lines)

def run_library_benchmark(library_db_path: Path = LIBRARY_DB_PATH) -> None:
    """从 library.db 抽取 Movie / Series / Episode 样本进行 MDBList 基准测试。"""
    if not library_db_path.is_file():
        print(f"Error: library.db not found at {library_db_path}")
        return

    conn = sqlite3.connect(library_db_path)
    cur = conn.cursor()

    movies = cur.execute(
        "SELECT title_id, primary_title FROM title_basic WHERE title_type = 'movie' LIMIT 3"
    ).fetchall()
    series = cur.execute(
        "SELECT title_id, primary_title FROM title_basic WHERE title_type = 'series' LIMIT 3"
    ).fetchall()
    episodes = cur.execute(
        "SELECT title_id, primary_title FROM title_basic WHERE title_type = 'episode' LIMIT 3"
    ).fetchall()
    conn.close()

    test_groups = [
        ("Movie (电影)", movies),
        ("Series (剧集)", series),
        ("Episode (单集)", episodes),
    ]

    print("\n" + "#" * 70)
    print("# MDBList 多平台影视评分抓取测试 (基于 library.db 样本)")
    print("#" * 70)

    for group_name, items in test_groups:
        print(f"\n>>>>>> 测试类别: {group_name} <<<<<<")
        for tid, name in items:
            summary = fetch_mdblist_ratings(tid)
            print(format_summary_text(summary))
            print()

# ==============================================================================
# CLI 入口
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="通过 IMDb Title ID 从 MDBList 获取多平台聚合评分 (纯函数式设计)"
    )
    parser.add_argument(
        "title_ids",
        nargs="*",
        help="一个或多个 IMDb ID (如 tt0816692, tt11280740, tt10359238)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="从 library.db 抽样测试 Movie、Series 和 Episode 并输出对比报告",
    )

    args = parser.parse_args()

    if args.benchmark:
        run_library_benchmark()
        return

    if not args.title_ids:
        sample_id = "tt0816692"
        print(f"未指定 IMDb ID，使用默认示例电影: {sample_id} (Interstellar)")
        args.title_ids = [sample_id]

    results = [fetch_mdblist_ratings(tid) for tid in args.title_ids]

    if args.json:
        dict_results = [asdict(r) for r in results]
        print(json.dumps(dict_results if len(dict_results) > 1 else dict_results[0], ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(format_summary_text(r))

if __name__ == "__main__":
    main()
