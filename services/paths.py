"""程序文件与用户数据的位置。

程序代码在 ``APP_DIR``（仓库根目录，或安装包里的 ``app/``）。用户数据——媒体库、识别缓存、
``.env``、海报与缩略图、IMDb 评分文件、日志——都放在 ``DATA_DIR``：

1. 设置了 ``MOVIEDB_DATA_DIR`` 时用它；
2. 免安装版（``app/`` 旁有 ``portable`` 标记）：沿用程序目录，整个文件夹可以随身带走；
3. 安装版（``app/`` 旁有 ``runtime/``）：Windows 为 ``%LOCALAPPDATA%\\MovieDB``，
   macOS 为 ``~/Library/Application Support/MovieDB``，Linux 为 ``$XDG_DATA_HOME/moviedb``；
4. 源码运行：仓库根目录（与以前一样，开发时不需要任何配置）。

海报与缩略图仍以 ``/static/images/…``、``/static/thumbnails/…`` 访问，实际文件在
``MEDIA_ROOT``（``DATA_DIR/static``）；源码运行时它就是仓库的 ``static/``。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = APP_DIR.parent / "runtime"
APP_STATIC = APP_DIR / "static"
IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
PACKAGED = RUNTIME_DIR.is_dir()
PORTABLE = (APP_DIR.parent / "portable").is_file()
BUNDLED_FFPROBE = RUNTIME_DIR / "ffmpeg" / ("ffprobe.exe" if IS_WINDOWS else "ffprobe")

# 迁移旧版安装目录里的数据时按这个清单搬（相对 DATA_DIR）。
DATA_ITEMS = ("library.db", "library.db-wal", "library.db-shm", "discovery.db", "discovery.db-wal",
              "discovery.db-shm", "api-usage.db", ".env", "static/images", "static/thumbnails", "imdb", "logs")


def user_data_dir() -> Path:
    """系统约定的用户数据目录（安装版使用）。"""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "MovieDB"
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "MovieDB"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "moviedb"


def resolve_data_dir() -> Path:
    configured = os.environ.get("MOVIEDB_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if PACKAGED and not PORTABLE:
        return user_data_dir()
    return APP_DIR


DATA_DIR = resolve_data_dir()
MEDIA_ROOT = DATA_DIR / "static"
IMAGE_ROOT = MEDIA_ROOT / "images"
THUMBNAIL_ROOT = MEDIA_ROOT / "thumbnails"
ENV_PATH = DATA_DIR / ".env"
ENV_EXAMPLE = APP_DIR / ".env.example"
LOG_PATH = DATA_DIR / "logs" / "moviedb.log"


def database_path() -> Path:
    return Path(os.environ.get("MOVIEDB_DATABASE") or DATA_DIR / "library.db")


def is_media_path(relative: str) -> bool:
    """/static/ 下的海报与缩略图（用户数据），其余是随程序发布的静态资源。"""
    return relative.replace("\\", "/").split("/", 1)[0] in ("images", "thumbnails")


def migrate_legacy(source: Path = APP_DIR, target: Path | None = None) -> list[str]:
    """把旧版放在程序目录里的数据搬到 DATA_DIR（只在目标还没有媒体库与配置时进行）。

    任何一项搬不动（例如旧版仍在运行、文件被占用）就把已搬的移回去，本次继续使用旧位置，
    下次启动再试；返回已搬过去的项目。
    """
    target = target or DATA_DIR
    if source.resolve() == target.resolve():
        return []
    if (target / "library.db").exists() or (target / ".env").exists():
        return []
    items = [name for name in DATA_ITEMS if (source / name).exists()]
    if not any(name in items for name in ("library.db", ".env")):
        return []
    moved: list[str] = []
    try:
        for name in items:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():  # 目标里已有的同名缓存目录（例如空的 logs）先让位
                if destination.is_dir() and not any(destination.iterdir()):
                    destination.rmdir()
                else:
                    continue
            shutil.move(str(source / name), str(destination))
            moved.append(name)
    except OSError as error:
        logger.warning("迁移数据到 %s 失败，继续使用 %s：%s", target, source, error)
        for name in reversed(moved):
            try:
                shutil.move(str(target / name), str(source / name))
            except OSError:
                logger.exception("回滚 %s 失败", name)
        raise
    return moved
