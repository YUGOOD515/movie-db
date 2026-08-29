import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import helpers
import models


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "movie.db"


def _probe_source(source: dict, token: str):
    video_url = helpers.get_video_link(source["alist_path"], token=token)
    return helpers.probe_video(video_url)


def backfill_media_metadata(
    database_path: str | Path,
    *,
    workers: int = 4,
    limit: int | None = None,
    progress_every: int = 25,
):
    sources = models.get_sources_missing_media_metadata(database_path)
    if limit is not None:
        sources = sources[: max(limit, 0)]
    if not sources:
        return {"total": 0, "succeeded": 0, "failed": 0}

    token = helpers.get_token(helpers.username, helpers.password)
    succeeded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as executor:
        futures = {
            executor.submit(_probe_source, source, token): source
            for source in sources
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            source = futures[future]
            try:
                metadata = future.result()
                models.save_source_media_metadata(
                    database_path,
                    source["media_type"],
                    source["source_id"],
                    metadata,
                )
            except Exception as error:
                failed += 1
                print(
                    f"[{completed}/{len(sources)}] failed "
                    f"{source['media_type']}:{source['source_id']} "
                    f"{source['alist_path']} — {error}",
                    flush=True,
                )
            else:
                succeeded += 1
                if completed % max(progress_every, 1) == 0 or completed == len(sources):
                    print(
                        f"[{completed}/{len(sources)}] "
                        f"{succeeded} saved, {failed} failed",
                        flush=True,
                    )

    return {
        "total": len(sources),
        "succeeded": succeeded,
        "failed": failed,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Backfill ffprobe metadata for stored AList sources."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--progress-every", type=int, default=25)
    arguments = parser.parse_args()

    try:
        result = backfill_media_metadata(
            arguments.database,
            workers=arguments.workers,
            limit=arguments.limit,
            progress_every=arguments.progress_every,
        )
    except Exception as error:
        parser.exit(1, f"backfill failed: {error}\n")
    print(
        "completed: "
        f"{result['succeeded']} succeeded, "
        f"{result['failed']} failed, "
        f"{result['total']} total",
        flush=True,
    )
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
