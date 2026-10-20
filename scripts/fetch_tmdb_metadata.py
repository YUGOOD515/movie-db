"""Fetch TMDB metadata by IMDb ID; optionally download artwork."""

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from connectors.tmdb import DEFAULT_IMAGE_ROOT, build_media_metadata, download_metadata_images


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通过 IMDb ID 从 TMDB 抓取元数据 JSON（与 library.db 字段对应，不含 IMDb 评分）。"
    )
    parser.add_argument("imdb_id", help="电影或剧集的 IMDb ID，例如 tt0816692")
    parser.add_argument("-o", "--output", type=Path, help="写入文件（默认打印到终端）")
    parser.add_argument(
        "--download",
        action="store_true",
        help="下载返回结果中的图片到 static/images（默认不启用）",
    )
    parser.add_argument(
        "--image-root",
        type=Path,
        default=DEFAULT_IMAGE_ROOT,
        help="图片下载根目录（默认 static/images）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    try:
        metadata = build_media_metadata(args.imdb_id)
    except (requests.RequestException, ValueError, RuntimeError) as error:
        print(f"抓取失败：{error}", file=sys.stderr)
        return 1

    if args.download:
        stats = download_metadata_images(metadata, args.image_root)
        print(
            f"图片下载完成：{stats['downloaded']} 张下载，"
            f"{stats['skipped']} 张已存在跳过，{stats['failed']} 张失败",
            file=sys.stderr,
        )

    text = json.dumps(metadata, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
