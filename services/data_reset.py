"""设置页“数据”分区：彻底删除本机数据，回到首次使用引导。

可选四项（默认全选）：媒体库、识别缓存与扫描记录、海报与缩略图、连接与密钥（.env）。
删除不可恢复、不做备份。清除媒体库且已开启云端同步时，先用一个空媒体库发布快照，
让云端同步清空（发布脚本会删除远端多余的对象），再删除 .env——发布需要其中的同步令牌。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from connectors import alist, throttle, tmdb
from models import discovery_store as store
from services import cloud_sync, media_discovery, setup_wizard

logger = logging.getLogger(__name__)

CONFIRM_WORD = "清除"
PARTS = (
    {"id": "library", "label": "媒体库", "icon": "collection-play",
     "hint": "作品、已关联的影片、观看进度与收藏"},
    {"id": "discovery", "label": "识别缓存与扫描记录", "icon": "radar",
     "hint": "扫描目录、识别结果、忽略列表与 TMDB 查询缓存"},
    {"id": "images", "label": "海报与缩略图", "icon": "images",
     "hint": "已下载的海报、剧照、头像和缩略图"},
    {"id": "config", "label": "连接与密钥", "icon": "key",
     "hint": "AList、TMDB、AI 等全部设置（.env）"},
)
PART_IDS = tuple(part["id"] for part in PARTS)


class ResetError(Exception):
    pass


def _targets(app) -> dict[str, list[Path]]:
    from models import api_usage
    database = Path(app.config["DATABASE"])
    discovery = store.store_path(database)
    sqlite_files = lambda path: [path, *(Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))]
    return {
        "library": sqlite_files(database),
        "discovery": [*sqlite_files(discovery), *sqlite_files(api_usage.database_path())],
        "images": [Path(app.config["IMAGE_ROOT"]), Path(app.config["THUMBNAIL_ROOT"])],
        "config": [setup_wizard.ENV_PATH],
    }


def _size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        pass
    return 0


def describe(app) -> dict:
    """设置页展示：每项的占用空间与是否存在，以及清除媒体库时是否会清空云端快照。"""
    targets = _targets(app)
    parts = []
    for part in PARTS:
        paths = targets[part["id"]]
        parts.append({**part, "size": sum(_size(path) for path in paths),
                      "exists": any(path.exists() for path in paths)})
    return {"parts": parts, "cloud": cloud_sync.is_enabled(), "confirm": CONFIRM_WORD}


def _remove(path: Path, failed: list[str]) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        # Windows 上文件被其他程序（或后台任务）占用时无法删除。
        logger.warning("Failed to remove %s: %s", path, error)
        failed.append(path.name)


def _clear_cloud() -> bool:
    with tempfile.TemporaryDirectory(prefix="moviedb-reset-") as folder:
        empty = Path(folder) / "library.db"
        setup_wizard.ensure_library(empty)
        return cloud_sync.run_publish(empty)


def _env_keys(path: Path) -> list[str]:
    try:
        from dotenv import dotenv_values
        return [key for key in dotenv_values(path) if key]
    except (ImportError, OSError):
        return []


def clear(app, parts) -> dict:
    """按所选项删除数据并重置进程内状态；部分文件被占用时返回 failed 列表。"""
    selected = [part for part in PART_IDS if part in set(parts or ())]
    if not selected:
        raise ResetError("请至少选择一项要清除的数据。")
    with media_discovery._lock:
        busy = any(state.get("status") in ("running", "recognizing") for state in media_discovery._scans.values())
    if busy:
        raise ResetError("正在扫描或识别影片，请等它结束后再清除。")
    from services import import_jobs
    if import_jobs.busy(app.config["DATABASE"]):
        raise ResetError("正在关联影片，请等任务结束后再清除。")

    targets = _targets(app)
    failed: list[str] = []
    # 先删图片和媒体库，再发布空快照：快照会带上 static 下的文件，远端多余的图片也一并删除。
    for part in ("images", "library", "discovery"):
        if part in selected:
            for path in targets[part]:
                _remove(path, failed)
    cloud = None
    if "library" in selected and cloud_sync.is_enabled():
        cloud = "cleared" if _clear_cloud() else "failed"
    if "config" in selected:
        keys = _env_keys(setup_wizard.ENV_PATH)
        _remove(setup_wizard.ENV_PATH, failed)
        if setup_wizard.ENV_PATH.name not in failed:
            for key in keys:
                os.environ.pop(key, None)

    # 进程内缓存与状态：按全新安装处理。
    with media_discovery._lock:
        media_discovery._scans.clear()
    media_discovery._autoscan_started.clear()
    store.reset()
    tmdb.clear_cache()
    throttle.reset()
    alist.forget_token()
    app.config.pop("LIBRARY_READY", None)
    if "config" in selected:
        app.config["ALIST_ORIGIN"] = os.environ.get("ALIST_ORIGIN", "http://127.0.0.1:5244").rstrip("/")

    restart = "library" in selected or "config" in selected
    return {"ok": not failed, "cleared": selected, "failed": failed, "cloud": cloud,
            "next": "/setup" if restart and not failed else None}
