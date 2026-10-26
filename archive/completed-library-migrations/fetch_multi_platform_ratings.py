"""Fetch MDBList ratings or benchmark a sample of the local library."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.mdblist import TitleRatingsSummary, fetch_mdblist_ratings

LIBRARY_DB_PATH = Path(__file__).resolve().parents[1] / "library.db"


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
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
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
