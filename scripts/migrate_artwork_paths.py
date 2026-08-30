"""一次性迁移：图片目录从 TMDB ID 命名改为 Title ID（IMDb ID）命名。

旧结构（TMDB ID 命名）：
    images/{tmdb_id}/{posters|backdrops|logos}/{1..5}.{jpg|png}          # 电影
    images/tv/{tmdb_id}/{posters|backdrops|logos}/{1..5}.{jpg|png}       # 剧集
    images/tv/{series_tmdb_id}/season-{N}/episodes/{episode_tmdb_id}.jpg # 单集剧照

新结构（Title ID 命名，电影与剧集平铺）：
    images/{title_id}/{posters|backdrops|logos}/{文件名保持不变}
    images/{series_title_id}/{episode_title_id}-still{ext}               # 单集剧照

说明：
- 文件名 1..5 保持原样（不按 position 重命名）。position 与文件的对应关系
  存在 title_artwork 表中（切换主图会交换 position 值而非重命名文件）。
- local_path 列保留，全部改写为新路径。
- 运行前自动备份 library.db 到 backups/。

用法：
    uv run python scripts/migrate_artwork_paths.py
    uv run python scripts/migrate_artwork_paths.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_DB = PROJECT_ROOT / "library.db"
IMAGE_ROOT = PROJECT_ROOT / "static" / "images"
BACKUPS_DIR = PROJECT_ROOT / "backups"

# artwork_type -> 新目录名（与磁盘目录一致）
TYPE_DIRECTORY = {
    "poster": "posters",
    "landscape_poster": "backdrops",
    "logo": "logos",
}

LOCAL_PREFIX = "images"


def _relative_to_image_root(local_path: str) -> Path:
    """images/xxx/... -> xxx/...（相对 IMAGE_ROOT）。"""
    return Path(*Path(local_path).parts[1:])


def _new_series_or_movie_path(title_id: str, artwork_type: str, local_path: str) -> str:
    parts = Path(local_path).parts
    directory = TYPE_DIRECTORY[artwork_type]
    filename = parts[-1]
    return f"{LOCAL_PREFIX}/{title_id}/{directory}/{filename}"


def _new_still_path(episode_id: str, series_id: str, local_path: str) -> str:
    suffix = Path(local_path).suffix or ".jpg"
    return f"{LOCAL_PREFIX}/{series_id}/{episode_id}-still{suffix}"


def migrate(dry_run: bool = False) -> int:
    connection = sqlite3.connect(LIBRARY_DB)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT a.title_id, a.artwork_type, a.position, a.local_path
          FROM title_artwork a
          JOIN title_basic b ON b.title_id = a.title_id
         ORDER BY a.title_id, a.artwork_type, a.position
        """
    ).fetchall()

    relations = {
        row["episode_id"]: row["series_id"]
        for row in connection.execute(
            "SELECT episode_id, series_id FROM title_relation"
        )
    }

    moved, missing, skipped, updated = 0, 0, 0, 0
    warnings: list[str] = []

    for row in rows:
        local_path = row["local_path"]
        if not local_path.startswith(f"{LOCAL_PREFIX}/"):
            warnings.append(f"[skip] 非标准路径: {row['title_id']} {local_path}")
            skipped += 1
            continue

        if row["artwork_type"] == "still":
            series_id = relations.get(row["title_id"])
            if series_id is None:
                warnings.append(f"[skip] 单集无归属关系: {row['title_id']}")
                skipped += 1
                continue
            new_path = _new_still_path(row["title_id"], series_id, local_path)
        else:
            new_path = _new_series_or_movie_path(
                row["title_id"], row["artwork_type"], local_path
            )

        if new_path == local_path:
            continue

        source = IMAGE_ROOT / _relative_to_image_root(local_path)
        target = IMAGE_ROOT / _relative_to_image_root(new_path)

        if source.exists():
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
            moved += 1
        else:
            warnings.append(f"[miss] 磁盘文件不存在: {local_path}")
            missing += 1

        if not dry_run:
            connection.execute(
                """
                UPDATE title_artwork
                   SET local_path = ?
                 WHERE title_id = ? AND artwork_type = ? AND position = ?
                """,
                (new_path, row["title_id"], row["artwork_type"], row["position"]),
            )
        updated += 1

    if not dry_run:
        connection.commit()
        _remove_empty_directories(IMAGE_ROOT)
    connection.close()

    print(f"迁移完成: 移动 {moved} 个文件, 缺失 {missing}, 跳过 {skipped}, DB 更新 {updated} 行")
    for warning in warnings:
        print(f"  {warning}")
    return 0


def _remove_empty_directories(root: Path):
    """自底向上删除空目录（旧 TMDB ID 目录、tv/、season-N 等）。"""
    removed = 0
    for path in sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if not any(path.iterdir()):
            path.rmdir()
            removed += 1
    print(f"清理空目录: {removed} 个")


def _backup_library() -> Path:
    BACKUPS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUPS_DIR / f"library_backup_before_artwork_paths_{timestamp}.db"
    shutil.copy2(LIBRARY_DB, backup_path)
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="只统计不执行")
    args = parser.parse_args()

    if not LIBRARY_DB.exists():
        print(f"未找到 {LIBRARY_DB}", file=sys.stderr)
        return 1

    if not args.dry_run:
        backup_path = _backup_library()
        print(f"已备份 library.db -> {backup_path}")

    return migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
