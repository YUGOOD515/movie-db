"""Small read-only task projections. Only `import_task_detail` (one task, on demand) reads file lists and probes."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time

from models import local_store


def import_tasks(database):
    path = local_store.store_path(database).resolve()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute("""
                SELECT job_id, title_id, state, progress, error, attempts, next_at, updated_at,
                       json_array_length(payload, '$.items') AS total,
                       json_extract(payload, '$.name') AS name,
                       COALESCE(json_extract(payload, '$.batch'), CAST(created_at AS TEXT)) AS batch,
                       json_extract(result, '$.warning') AS warning
                  FROM import_job
                 WHERE library_path = ?
                   AND (state IN ('running', 'queued') OR updated_at >= ?)
                 ORDER BY CASE WHEN state IN ('running', 'queued') THEN 0 ELSE 1 END,
                          created_at DESC, job_id
                 LIMIT 1000
            """, (str(Path(database).resolve()), time.time() - 86400))]
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
            return []


def title_names(database, ids):
    if not ids:
        return {}
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return dict(db.execute(f"SELECT title_id, display_title FROM title_basic WHERE title_id IN ({','.join('?' for _ in ids)})", list(ids)))


def import_task_detail(database, title_id, batch):
    """One sidebar task's jobs, including file lists and probe checkpoints (detail drawer only)."""
    path = local_store.store_path(database).resolve()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute("""
                SELECT job_id, title_id, state, progress, probes, payload, result, error, attempts, next_at
                  FROM import_job
                 WHERE library_path = ?
                   AND title_id = ?
                   AND COALESCE(json_extract(payload, '$.batch'), CAST(created_at AS TEXT)) = ?
                 ORDER BY created_at, job_id
            """, (str(Path(database).resolve()), title_id, batch))]
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
            return []


def title_brief(database, title_id):
    """Name, type, year and first poster of a title already in the library, or None."""
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("""
            SELECT title.title_id,
                   title.display_title,
                   title.title_type,
                   title.start_year,
                   (SELECT artwork.local_path
                      FROM title_artwork AS artwork
                     WHERE artwork.title_id = title.title_id
                       AND artwork.artwork_type = 'poster'
                     ORDER BY artwork.position
                     LIMIT 1) AS poster_path
              FROM title_basic AS title
             WHERE title.title_id = ?
        """, (title_id,)).fetchone()
    return dict(row) if row else None
