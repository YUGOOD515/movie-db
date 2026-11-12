"""把已下载的超宽原图（多为 4K 剧照、背景图与标志）限宽重存，释放磁盘空间。

新导入的图片在下载时已自动处理，这个脚本只用于处理以前下载的存量图片。
默认只统计，加 --apply 才改写文件；改写后重新生成对应的缩略图，色调表在下次启动时自动补齐。

    uv run python scripts/compact_images.py            # 只统计
    uv run python scripts/compact_images.py --apply    # 改写（不可恢复，建议先备份 static/images）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from services import paths  # noqa: E402
from services.thumbnails import ORIGINAL_MAX_WIDTH, ensure_thumbnail, image_kind, shrink_original  # noqa: E402


def oversized(image_root: Path):
    """[(文件, local_path, 类型, 宽度)]：宽度超过上限的原图。"""
    from PIL import Image

    for path in sorted(image_root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        local_path = f"images/{path.relative_to(image_root).as_posix()}"
        kind = image_kind(local_path)
        if kind is None:
            continue
        try:
            with Image.open(path) as image:
                width = image.width
        except OSError:
            continue
        if width > ORIGINAL_MAX_WIDTH[kind]:
            yield path, local_path, kind, width


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="改写文件（默认只统计）")
    args = parser.parse_args()

    before = after = changed = 0
    counts: dict[str, int] = {}
    for path, local_path, kind, _width in oversized(paths.IMAGE_ROOT):
        size = path.stat().st_size
        before += size
        counts[kind] = counts.get(kind, 0) + 1
        if args.apply and shrink_original(path, local_path):
            changed += 1
            after += path.stat().st_size
            ensure_thumbnail(local_path, static_root=paths.MEDIA_ROOT)
        elif args.apply:
            after += size

    total = sum(counts.values())
    detail = "、".join(f"{kind} {count} 张" for kind, count in sorted(counts.items())) or "无"
    print(f"超过宽度上限的原图：{total} 张（{detail}），共 {before / 1048576:.1f} MB")
    if args.apply:
        print(f"已改写 {changed} 张，现占 {after / 1048576:.1f} MB，释放 {(before - after) / 1048576:.1f} MB")
    elif total:
        print("未改写任何文件；加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
