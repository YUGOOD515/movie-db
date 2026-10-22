"""Standalone CLI script to build and update the lightweight thumbnail cache.

Usage:
    python scripts/build_thumbnails.py --all
    python scripts/build_thumbnails.py --title tt1870479
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services import paths as app_paths
from services.thumbnails import (
    DEFAULT_STATIC_ROOT,
    STILL_SIZE,
    BACKDROP_CARD_SIZE,
    LOGO_CARD_SIZE,
    get_target_size_for_path,
    generate_thumbnail,
    normalize_image_path,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def build_thumbnails_for_paths(
    local_paths: list[str],
    static_root: Path = DEFAULT_STATIC_ROOT,
    force: bool = False,
) -> dict:
    stats = {
        "scanned": 0,
        "created": 0,
        "skipped": 0,
        "failed": 0,
        "orig_bytes": 0,
        "thumb_bytes": 0,
        "samples": [],
    }

    for rel_path in local_paths:
        if not rel_path:
            continue
        stats["scanned"] += 1
        relative = normalize_image_path(rel_path)
        if relative is None:
            stats["failed"] += 1
            continue
        rel_path = relative.as_posix()
        target_size = get_target_size_for_path(rel_path)
        if target_size is None:
            continue

        source_path = static_root.joinpath(*relative.parts)
        thumb_rel = f"thumbnails/{rel_path}"
        target_path = static_root / "thumbnails" / Path(*relative.parts)

        if not source_path.is_file():
            stats["skipped"] += 1
            continue

        s_stat = source_path.stat()
        orig_size = s_stat.st_size

        if not force and target_path.is_file():
            t_stat = target_path.stat()
            if t_stat.st_mtime >= s_stat.st_mtime:
                stats["skipped"] += 1
                stats["orig_bytes"] += orig_size
                stats["thumb_bytes"] += t_stat.st_size
                continue

        success = generate_thumbnail(source_path, target_path, target_size)
        if success and target_path.is_file():
            stats["created"] += 1
            thumb_size = target_path.stat().st_size
            stats["orig_bytes"] += orig_size
            stats["thumb_bytes"] += thumb_size
            if len(stats["samples"]) < 10:
                stats["samples"].append({
                    "path": rel_path,
                    "target_size": f"{target_size[0]}x{target_size[1]}",
                    "orig_bytes": orig_size,
                    "thumb_bytes": thumb_size,
                })
        else:
            stats["failed"] += 1

    return stats


def get_all_artwork_paths(db_path: Path) -> list[str]:
    if not db_path.is_file():
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT local_path
              FROM title_artwork
             WHERE local_path IS NOT NULL
               AND artwork_type IN ('still', 'landscape_poster', 'logo')
            """
        ).fetchall()
        return [row["local_path"] for row in rows]


def get_title_artwork_paths(db_path: Path, title_id: str) -> list[str]:
    if not db_path.is_file():
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT local_path
              FROM title_artwork
             WHERE title_id = ?
                OR title_id IN (
                    SELECT episode_id FROM title_relation WHERE series_id = ?
                )
            """,
            (title_id, title_id),
        ).fetchall()
        return [row["local_path"] for row in rows if row["local_path"]]


def main():
    parser = argparse.ArgumentParser(description="Build and update image thumbnail cache.")
    parser.add_argument("--all", action="store_true", help="Build thumbnails for all artwork in database.")
    parser.add_argument("--title", type=str, help="Build thumbnails for a specific title (movie or series ID).")
    parser.add_argument("--force", action="store_true", help="Force regenerate existing thumbnails.")
    parser.add_argument(
        "--db",
        type=str,
        default=str(app_paths.database_path()),
        help="Path to library.db (defaults to the app data folder)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if args.title:
        paths = get_title_artwork_paths(db_path, args.title)
        print(f"Building thumbnails for {args.title} ({len(paths)} artwork records)...")
    elif args.all:
        paths = get_all_artwork_paths(db_path)
        print(f"Building thumbnails for all media ({len(paths)} artwork records)...")
    else:
        # Default: scan static/images/ directory
        img_root = DEFAULT_STATIC_ROOT / "images"
        paths = [
            p.relative_to(DEFAULT_STATIC_ROOT).as_posix()
            for p in img_root.rglob("*.*")
            if p.suffix.lower() in (".jpg", ".jpeg", ".png") and "thumbnails" not in p.parts
        ]
        print(f"Scanning static/images directory ({len(paths)} image files)...")

    stats = build_thumbnails_for_paths(paths, static_root=DEFAULT_STATIC_ROOT, force=args.force)

    print("\n" + "="*70)
    print("THUMBNAIL GENERATION SUMMARY")
    print("="*70)
    print(f"  - Scanned images:     {stats['scanned']}")
    print(f"  - Created/Updated:    {stats['created']}")
    print(f"  - Skipped (valid):    {stats['skipped']}")
    print(f"  - Failed:             {stats['failed']}")
    if stats["orig_bytes"]:
        reduction = (1.0 - (stats["thumb_bytes"] / stats["orig_bytes"])) * 100.0
        print(f"  - Original size:      {stats['orig_bytes'] / (1024*1024):.2f} MB")
        print(f"  - Thumbnail size:     {stats['thumb_bytes'] / (1024*1024):.2f} MB")
        print(f"  - Space reduction:    {reduction:.1f}%")

    if stats["samples"]:
        print("\nSample Generated Thumbnails:")
        for s in stats["samples"][:5]:
            print(f"  - {s['path']} ({s['target_size']}): {s['orig_bytes']/1024:.1f} KB -> {s['thumb_bytes']/1024:.1f} KB")


if __name__ == "__main__":
    main()
