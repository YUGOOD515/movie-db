from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from threading import Lock, Thread

import logging
from contextlib import closing

from connectors import imdb_ratings, mdblist
from models.database import connect_database
from models.library import update_imdb_ratings, update_title_ratings
from queries.manager import get_all_title_ids


DEFAULT_WORKERS = 3
logger = logging.getLogger(__name__)
_job_lock = Lock()
_job_state = {
    "running": False,
    "total": 0,
    "completed": 0,
    "succeeded": 0,
    "failed": 0,
    "current_title_id": None,
}


def extract_rating_fields(item: dict) -> dict:
    fields = {
        "mdblist_score": item.get("score"),
        "mdblist_score_average": item.get("score_average"),
        "rt_critics_score": None,
        "rt_critics_votes": None,
        "rt_audience_score": None,
        "rt_audience_votes": None,
        "metacritic_score": None,
        "metacritic_votes": None,
        "metacritic_user_score": None,
        "metacritic_user_votes": None,
        "trakt_score": None,
        "trakt_votes": None,
        "letterboxd_score": None,
        "letterboxd_votes": None,
        "roger_ebert_rating": None,
        "myanimelist_score": None,
        "myanimelist_votes": None,
    }
    for rating in item.get("platform_ratings") or []:
        key = rating.get("platform_key")
        votes = rating.get("votes")
        score_10 = rating.get("score_10")
        score_100 = rating.get("score_100")
        display = rating.get("score_display") or ""
        if key == "tomatoes_critic":
            fields["rt_critics_score"] = _integer(score_100)
            fields["rt_critics_votes"] = votes
        elif key == "tomatoes_audience":
            fields["rt_audience_score"] = _integer(score_100)
            fields["rt_audience_votes"] = votes
        elif key == "metacritic_critic":
            fields["metacritic_score"] = _integer(score_100)
            fields["metacritic_votes"] = votes
        elif key == "metacritic_user":
            fields["metacritic_user_score"] = _number(score_10)
            fields["metacritic_user_votes"] = votes
        elif key == "trakt":
            fields["trakt_score"] = _integer(score_100)
            fields["trakt_votes"] = votes
        elif key == "letterboxd":
            fields["letterboxd_score"] = _display_number(display, score_10, 2)
            fields["letterboxd_votes"] = votes
        elif key == "rogerebert":
            fields["roger_ebert_rating"] = _display_number(display)
        elif key == "myanimelist":
            fields["myanimelist_score"] = _number(score_10)
            fields["myanimelist_votes"] = votes
    return fields


def _integer(value):
    return int(value) if value is not None else None


def _number(value):
    return float(value) if value is not None else None


def _display_number(display: str, fallback=None, divisor: float = 1):
    if "/" in display:
        try:
            return float(display.split("/", 1)[0])
        except ValueError:
            pass
    return float(fallback) / divisor if fallback is not None else None


def fetch_title_rating(title_id: str) -> dict:
    try:
        summary = mdblist.fetch_title_ratings(title_id)
    except Exception as error:
        return {
            "title_id": title_id,
            "status": "error",
            "ratings": {},
            "message": str(error),
        }
    if summary.status != "success":
        return {
            "title_id": title_id,
            "status": summary.status,
            "ratings": {},
            "message": summary.error_message,
            "retry_after": summary.retry_after,
        }
    return {
        "title_id": title_id,
        "status": "success",
        "ratings": extract_rating_fields(asdict(summary)),
        "message": None,
    }


def refresh_imdb_ratings(database_path: str | Path) -> int:
    """用 IMDb 官方评分文件更新库内全部作品（含单集），返回变化的行数。"""
    with closing(connect_database(database_path)) as db:
        title_ids = get_all_title_ids(db)
    return update_imdb_ratings(database_path, imdb_ratings.lookup_ratings(title_ids))


def sync_title_ratings(
    database_path: str | Path,
    title_ids: list[str],
    *,
    max_workers: int = DEFAULT_WORKERS,
    progress_callback=None,
) -> dict:
    unique_ids = list(dict.fromkeys(title_ids))
    result = {"total": len(unique_ids), "completed": 0, "succeeded": 0, "failed": 0}
    if not unique_ids:
        return result

    workers = max(1, min(int(max_workers), len(unique_ids)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_title_rating, title_id): title_id
            for title_id in unique_ids
        }
        for future in as_completed(futures):
            title_id = futures[future]
            try:
                fetched = future.result()
                updated = fetched["status"] == "success" and update_title_ratings(
                    database_path,
                    title_id,
                    fetched["ratings"],
                )
            except Exception:
                updated = False
            result["completed"] += 1
            result["succeeded" if updated else "failed"] += 1
            if progress_callback:
                progress_callback({**result, "current_title_id": title_id})
    return result


def start_rating_refresh(
    database_path: str | Path,
    title_ids: list[str],
) -> dict:
    with _job_lock:
        if _job_state["running"]:
            return {**_job_state, "started": False}
        _job_state.update(
            running=True,
            total=len(title_ids),
            completed=0,
            succeeded=0,
            failed=0,
            current_title_id=None,
        )
    worker = Thread(
        target=_run_rating_refresh,
        args=(Path(database_path), list(title_ids)),
        daemon=True,
        name="mdblist-rating-refresh",
    )
    worker.start()
    return {**get_rating_refresh_status(), "started": True}


def _run_rating_refresh(database_path: Path, title_ids: list[str]) -> None:
    def update_progress(progress: dict) -> None:
        with _job_lock:
            _job_state.update(progress)

    try:
        refresh_imdb_ratings(database_path)
    except Exception as error:
        logger.warning("IMDb 评分刷新失败：%s", error)
    try:
        result = sync_title_ratings(
            database_path,
            title_ids,
            progress_callback=update_progress,
        )
    except Exception:
        with _job_lock:
            completed = _job_state["completed"]
            succeeded = _job_state["succeeded"]
            failed = _job_state["failed"]
        result = {
            "total": len(title_ids),
            "completed": len(title_ids),
            "succeeded": succeeded,
            "failed": failed + len(title_ids) - completed,
        }
    with _job_lock:
        _job_state.update(result, running=False, current_title_id=None)


def get_rating_refresh_status() -> dict:
    with _job_lock:
        return dict(_job_state)
