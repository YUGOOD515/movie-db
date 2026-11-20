"""IMDb 本地库：单集编号与评分共用一次下载、一个数据库和一个更新状态；后台更新与定时检查。"""

import logging
import os
from pathlib import Path
import shutil
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


# 合并前分别保存的单集库与评分文件；新库发布后删除
LEGACY_FILES = ("episodes.sqlite3", "title.ratings.tsv.gz", "title.ratings.tsv.gz.part", "title.ratings.tsv.gz.sqlite3")
# (文件名, 下载地址, 元数据中保存 ETag 的键)
SOURCES = (("episodes.tsv.gz", imdb_episodes.URL, "etag"),
           ("ratings.tsv.gz", imdb_episodes.RATINGS_URL, "ratings_etag"))


def index_path():
    return paths.DATA_DIR / "imdb" / "index.sqlite3"


def episode_ids(parent):
    return store.lookup(index_path(), parent)


def ratings(title_ids):
    """{title_id: (评分, 票数)}；本地库尚未建立时返回空字典。"""
    return store.ratings(index_path(), title_ids)


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


def _download_all(path, folder, previous, conditional):
    """依次下载两个文件；进度按累计字节显示。返回 {文件名: 下载结果}。"""
    results, done = {}, 0
    for name, url, key in SOURCES:
        offset = done
        result = imdb_episodes.download(folder / name, url=url, etag=previous.get(key) if conditional else None,
            progress=lambda received, total: _progress(path, downloaded=offset + received, download_total=total))
        done += result.get("download_bytes", 0)
        results[name] = result
    return results


def _refresh(path, database):
    previous = store.info(path) or {}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 上次更新中途退出时留下的临时目录（同一时间只有一个更新任务，可以直接删）
        for stale in (*path.parent.glob(".imdb-update-*"), *path.parent.glob(".episodes-update-*")):
            shutil.rmtree(stale, ignore_errors=True)
        with tempfile.TemporaryDirectory(prefix=".imdb-update-", dir=path.parent) as folder:
            folder = Path(folder)
            results = _download_all(path, folder, previous, conditional=True)
            now = time.time()
            if all(result["unchanged"] for result in results.values()):
                store.checked(path, now)
                _progress(path, phase="complete", message="已是最新版本", finished_at=now)
                return
            if any(result["unchanged"] for result in results.values()):
                results = _download_all(path, folder, previous, conditional=False)  # 两个文件要一起建库
            episodes, rating_file = (folder / name for name, _url, _key in SOURCES)
            metadata = {"etag": results["episodes.tsv.gz"].get("etag"),
                        "ratings_etag": results["ratings.tsv.gz"].get("etag"),
                        "source_modified": results["episodes.tsv.gz"].get("source_modified"),
                        "revision": uuid.uuid4().hex, "updated_at": now, "checked_at": now}
            _progress(path, phase="build", message="正在建立索引", processed_rows=0)
            target = folder / "index.sqlite3"
            count = store.build(episodes, target, metadata, progress=lambda rows: _progress(path, processed_rows=rows),
                                ratings_source=rating_file)
            if previous.get("rows", 0) > 1000 and count < previous["rows"] * .9:
                raise ValueError("单集数据量异常，已保留旧版本。")
            store.publish(target, path)
        for name in LEGACY_FILES:
            (path.parent / name).unlink(missing_ok=True)
        _progress(path, phase="complete", message="IMDb 数据已更新", finished_at=time.time())
        if database is not None:
            _apply(database)
    except (OSError, ValueError, requests.RequestException) as error:
        logger.warning("IMDb 数据更新失败：%s", type(error).__name__)
        message = str(error) if isinstance(error, ValueError) else "IMDb 数据更新失败，请检查网络和磁盘后重试。"
        _progress(path, phase="failed", error=message, message=message, finished_at=time.time())
    except Exception:
        logger.exception("IMDb 数据更新失败")
        _progress(path, phase="failed", error="无法建立 IMDb 本地库，旧版本仍可使用。", finished_at=time.time())
    finally:
        _progress(path, running=False)


def _apply(database):
    """新库发布后：库内评分立即同步，单集编号变化通过重新扫描生效（不自动关联入库）。"""
    from services import rating_sync
    from services.media_discovery import has_scan_scope, start_scan
    try:
        rating_sync.refresh_imdb_ratings(database)
    except Exception:
        logger.exception("IMDb 评分写入媒体库失败")
    # 首次使用时 IMDb 本地库常在引导期间建好，这时还没选扫描目录：不扫，免得留下“还没有选择扫描目录”的错误
    if has_scan_scope(database):
        start_scan(database)


def start_update(database=None):
    path = index_path()
    with _lock:
        if _jobs.get(str(path), {}).get("running"):
            return False
        _jobs[str(path)] = {"running": True, "phase": "download", "message": "正在检查并下载",
                           "started_at": time.time(), "downloaded": 0, "download_total": 0}
    Thread(target=_refresh, args=(path, database), name="imdb-update", daemon=True).start()
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
                logger.exception("无法检查 IMDb 数据更新")
            wake.wait(60)
            wake.clear()
    Thread(target=run, name="imdb-scheduler", daemon=True).start()


def wake_scheduler(database):
    start_scheduler(database)
    with _lock:
        _schedulers[str(index_path())].set()
