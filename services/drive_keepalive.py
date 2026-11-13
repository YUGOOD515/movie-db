"""网盘凭据保活：定期调用各驱动的 keepalive()，让会话 Cookie 与令牌不过期。

MovieDB 不是常驻服务，关掉期间凭据照样会过期：启动后先全部执行一次，之后按驱动给出的间隔
（加 ±5% 随机抖动，避免集中请求）执行。驱动实例取自 media_source，与播放共用，
续期得到的新凭据立即对播放生效，并由驱动写回 local.db。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from datetime import datetime, timezone

from models import drive_store
from services import media_source

logger = logging.getLogger(__name__)

STARTUP_DELAY = 10  # 秒：避开启动时的扫描与导入恢复
TICK = 60

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_due: dict[str, float] = {}
_state: dict[str, dict] = {}
_clock = time.monotonic


def run_due(now: float | None = None) -> list[str]:
    """执行所有到期的保活，返回本次处理的挂载名。"""
    now = _clock() if now is None else now
    handled = []
    for row in drive_store.list_drives():
        mount = row["mount"]
        if not row["enabled"]:
            _due.pop(mount, None)
            continue
        if _due.get(mount, 0) > now:
            continue
        try:
            drive = media_source.drive_for(mount)
        except Exception:  # 刚被停用或删除
            continue
        interval = getattr(drive, "keepalive_interval", 0)
        if not interval:
            continue
        _due[mount] = now + interval * (1 + random.uniform(-0.05, 0.05))
        _keep(mount, drive)
        handled.append(mount)
    return handled


def _keep(mount, drive):
    from services.drives import describe_error

    try:
        status, ok = drive.keepalive(), True
    except Exception as error:
        status, ok = describe_error(error), False
        logger.warning("网盘 %s 续期失败：%s", mount, status)
    drive_store.set_status(mount, status)
    with _lock:
        _state[mount] = {"at": datetime.now(timezone.utc).isoformat(), "ok": ok}


def state(mount: str) -> dict:
    """最近一次保活：{"at": ISO 时间, "ok": 是否成功}；本次启动还没执行过时为空。"""
    with _lock:
        return dict(_state.get(mount) or {})


def forget(mount: str | None = None) -> None:
    """挂载被编辑、启用或删除后，下一轮立即重新保活。"""
    with _lock:
        if mount is None:
            _due.clear()
            _state.clear()
        else:
            _due.pop(mount, None)
            _state.pop(mount, None)


def _loop():
    if _stop.wait(STARTUP_DELAY):
        return
    while True:
        try:
            run_due()
        except Exception:
            logger.exception("网盘保活失败")
        if _stop.wait(TICK):
            return


def start() -> None:
    global _thread
    if os.environ.get("MOVIEDB_KEEPALIVE_DISABLED"):
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="drive-keepalive", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
