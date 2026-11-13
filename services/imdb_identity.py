"""本地单集库：后台更新、定时检查及供元数据服务读取的 IMDb ID 回退。"""

import logging
import os
from pathlib import Path
import tempfile
from threading import Event, RLock, Thread
import time
import uuid

import requests

from connectors import imdb_episodes
from models import imdb_episodes as store
from services import paths

logger = logging.getLogger(__name__)
_lock = RLock()
_jobs = {}
_schedulers = {}


def index_path():
    return paths.DATA_DIR / "imdb" / "episodes.sqlite3"


def episode_ids(parent):
    return store.lookup(index_path(), parent)


def revision():
    return (store.info(index_path()) or {}).get("revision", "missing")


def interval():
    return {"manual": 0, "daily": 86400}.get(os.environ.get("IMDB_EPISODES_UPDATE", ""), 7 * 86400)


def status():
    path = index_path()
    metadata = store.info(path) or {}
    with _lock:
        job = dict(_jobs.get(str(path), {}))
    return {**metadata, **job, "available": bool(metadata), "path": str(path),
            "running": bool(job.get("running")), "interval_seconds": interval(),
            "next_check": (metadata.get("checked_at", 0) + interval()) if interval() and metadata else None}


def _progress(path, **values):
    with _lock:
        _jobs[str(path)].update(values)


def _refresh(path, database):
    previous = store.info(path) or {}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".episodes-update-", dir=path.parent) as folder:
            folder = Path(folder)
            source, target = folder / "episodes.tsv.gz", folder / "episodes.sqlite3"
            result = imdb_episodes.download(source, etag=previous.get("etag"),
                progress=lambda received, total: _progress(path, downloaded=received, download_total=total))
            now = time.time()
            if result["unchanged"]:
                store.checked(path, now)
                _progress(path, phase="complete", message="已是最新版本", finished_at=now)
            else:
                _progress(path, phase="build", message="正在建立索引", processed_rows=0)
                count = store.build(source, target, {**result, "revision": uuid.uuid4().hex,
                    "updated_at": now, "checked_at": now}, progress=lambda rows: _progress(path, processed_rows=rows))
                if previous.get("rows", 0) > 1000 and count < previous["rows"] * .9:
                    raise ValueError("单集数据量异常，已保留旧版本。")
                store.publish(target, path)
                _progress(path, phase="complete", message="单集库已更新", finished_at=time.time())
                if database is not None:
                    from services.media_discovery import start_scan
                    start_scan(database)
    except (OSError, ValueError, requests.RequestException) as error:
        logger.warning("IMDb 单集库更新失败：%s", type(error).__name__)
        message = str(error) if isinstance(error, ValueError) else "IMDb 单集库更新失败，请检查网络和磁盘后重试。"
        _progress(path, phase="failed", error=message, message=message, finished_at=time.time())
    except Exception:
        logger.exception("IMDb 单集库更新失败")
        _progress(path, phase="failed", error="无法建立单集库，旧版本仍可使用。", finished_at=time.time())
    finally:
        _progress(path, running=False)


def start_update(database=None):
    path = index_path()
    with _lock:
        if _jobs.get(str(path), {}).get("running"):
            return False
        _jobs[str(path)] = {"running": True, "phase": "download", "message": "正在检查并下载",
                           "started_at": time.time(), "downloaded": 0, "download_total": 0}
    Thread(target=_refresh, args=(path, database), name="imdb-episodes-update", daemon=True).start()
    return True


def due(now=None):
    seconds = interval()
    if not seconds:
        return False
    now = time.time() if now is None else now
    state = status()
    if state["running"] or (state.get("error") and now - state.get("finished_at", 0) < 3600):
        return False
    return not state["available"] or now - state.get("checked_at", 0) >= seconds


def start_scheduler(database):
    key = str(index_path())
    with _lock:
        if key in _schedulers:
            return
        wake = _schedulers[key] = Event()

    def run():
        while True:
            try:
                if due():
                    start_update(database)
            except Exception:
                logger.exception("无法检查 IMDb 单集库更新")
            wake.wait(60)
            wake.clear()
    Thread(target=run, name="imdb-episodes-scheduler", daemon=True).start()


def wake_scheduler(database):
    start_scheduler(database)
    with _lock:
        _schedulers[str(index_path())].set()
