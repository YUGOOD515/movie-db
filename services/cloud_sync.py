"""本地数据库变化后，自动把只读快照同步到 Cloudflare。

仅在设置 MOVIEDB_SYNC_TOKEN 时启用。后台线程定期检查 library.db（含 WAL）
是否变化；变化稳定一段时间后（避免播放进度连续写入时反复同步），在子进程中
运行 scripts/publish_snapshot.py。启动时也会同步一次，以覆盖代码更新。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BASE_DIR / "scripts" / "publish_snapshot.py"
POLL_SECONDS = 15
STABLE_SECONDS = 60
MIN_INTERVAL_SECONDS = 120
TIMEOUT_SECONDS = 1800

_started = False
_lock = threading.Lock()


def is_enabled() -> bool:
    return bool(os.environ.get("MOVIEDB_SYNC_TOKEN", "").strip()) and \
        os.environ.get("MOVIEDB_SYNC_DISABLED", "").strip() != "1"


def database_signature(database: Path) -> tuple:
    signature = []
    # 影像色调表单独生成，变化后同样需要重新同步。
    for path in (database, Path(f"{database}-wal"), BASE_DIR / "static" / "thumbnails" / "tones.json"):
        try:
            stat = path.stat()
            signature.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append(None)
    return tuple(signature)


def run_publish(database: Path) -> bool:
    environment = dict(os.environ, MOVIEDB_SOURCE_DATABASE=str(database))
    for name in ("MOVIEDB_READ_ONLY", "MOVIEDB_DATABASE", "MOVIEDB_SNAPSHOT_AT"):
        environment.pop(name, None)
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            # 桌面版用 pythonw 运行，没有控制台；避免同步时弹出黑色窗口
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.warning("Snapshot sync failed to run: %s", error)
        return False
    if result.returncode != 0:
        logger.warning("Snapshot sync failed: %s", (result.stderr or result.stdout)[-2000:])
        return False
    logger.info("Snapshot synced: %s", result.stdout.strip()[-500:])
    return True


def _watch(database: Path) -> None:
    published = None
    last_change = time.monotonic()
    last_run = 0.0
    previous = database_signature(database)
    while True:
        current = database_signature(database)
        now = time.monotonic()
        if current != previous:
            previous = current
            last_change = now
        due = (
            current != published
            and now - last_change >= (0 if published is None else STABLE_SECONDS)
            and now - last_run >= (0 if published is None else MIN_INTERVAL_SECONDS)
        )
        if due:
            last_run = now
            if run_publish(database):
                published = current
            else:
                # 失败（如网络中断）后按最小间隔重试。
                published = published or ()
        time.sleep(POLL_SECONDS)


def init_app(app) -> None:
    global _started
    if app.config.get("TESTING") or app.config.get("READ_ONLY") or not is_enabled():
        return
    with _lock:
        if _started:
            return
        _started = True
    database = Path(app.config["DATABASE"])
    threading.Thread(target=_watch, args=(database,), name="snapshot-sync", daemon=True).start()
