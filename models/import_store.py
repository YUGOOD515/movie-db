"""SQLite storage for local association jobs, reservations and probe checkpoints."""

import hashlib
import json
import re
import time
import uuid
from contextlib import closing
from pathlib import Path

from models import discovery_store as store

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_job (
    job_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
    library_path TEXT NOT NULL, image_root TEXT NOT NULL, title_id TEXT NOT NULL,
    payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
    progress TEXT NOT NULL DEFAULT '{}', probes TEXT NOT NULL DEFAULT '{}',
    result TEXT, error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
    next_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
    owner TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS import_job_ready ON import_job(library_path, state, next_at);
CREATE TABLE IF NOT EXISTS import_reservation (
    library_path TEXT NOT NULL, path TEXT NOT NULL, job_id TEXT NOT NULL,
    PRIMARY KEY(library_path, path)
);
"""


def _connect(database):
    db = store._connect(database)
    db.executescript(SCHEMA)
    return db


def _public(row):
    result = json.loads(row["result"]) if row["result"] else None
    payload = json.loads(row["payload"])
    return {"id": row["job_id"], "title_id": row["title_id"], "state": row["state"],
            "total": len(payload["items"]), "paths": [item["path"] for item in payload["items"]],
            "progress": json.loads(row["progress"]), "result": result, "error": row["error"],
            "next_at": row["next_at"], "attempts": row["attempts"]}


def status(database, ids=None):
    library = str(Path(database).resolve())
    with closing(_connect(database)) as db:
        if ids is not None:
            if not isinstance(ids, list) or len(ids) > 1000 or any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{32}", item) for item in ids):
                raise ValueError("任务 ID 无效。")
            rows = db.execute(f"SELECT * FROM import_job WHERE library_path = ? AND job_id IN ({','.join('?' for _ in ids)}) ORDER BY created_at, job_id", (library, *ids)).fetchall() if ids else []
        else:
            rows = db.execute("SELECT * FROM import_job WHERE library_path = ? ORDER BY CASE WHEN state IN ('queued', 'running') THEN 0 ELSE 1 END, created_at DESC LIMIT 1000", (library,)).fetchall()
    return {"jobs": [_public(row) for row in rows]}


def _claim(database, owner_prefix):
    now, library = time.time(), str(Path(database).resolve())
    with closing(_connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE import_job SET state = CASE WHEN attempts >= 3 THEN 'failed' ELSE 'queued' END, owner = '', lease_until = 0 WHERE library_path = ? AND state = 'running' AND lease_until < ?", (library, now))
        db.execute("DELETE FROM import_reservation WHERE job_id IN (SELECT job_id FROM import_job WHERE state IN ('failed', 'cancelled', 'complete'))")
        row = db.execute("""SELECT job.* FROM import_job AS job
            WHERE job.library_path = ? AND job.state = 'queued' AND job.next_at <= ?
              AND NOT EXISTS (SELECT 1 FROM import_job AS active WHERE active.library_path = job.library_path
                              AND active.title_id = job.title_id AND active.state = 'running')
            ORDER BY job.created_at, job.job_id LIMIT 1""", (library, now)).fetchone()
        if row:
            owner = f"{owner_prefix}:{uuid.uuid4().hex}"
            db.execute("UPDATE import_job SET state = 'running', owner = ?, lease_until = ?, attempts = attempts + 1, updated_at = ? WHERE job_id = ?", (owner, now + 120, now, row["job_id"]))
            return {**dict(row), "owner": owner, "attempts": row["attempts"] + 1}


def _progress(database, job_id, owner, phase, done, total):
    # Retain independent counters: link and probe events now interleave.
    with closing(_connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT progress FROM import_job WHERE job_id = ? AND owner = ? AND state = 'running'", (job_id, owner)).fetchone()
        if row:
            progress = json.loads(row[0])
            progress.update(phase=phase, total=total)
            progress[phase] = done
            if phase == "enrich":
                progress["core"] = done
            db.execute("UPDATE import_job SET progress = ?, updated_at = ? WHERE job_id = ?", (json.dumps(progress), time.time(), job_id))


def _checkpoint(database, job_id, owner, path, probe):
    with closing(_connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT probes FROM import_job WHERE job_id = ? AND owner = ? AND state = 'running'", (job_id, owner)).fetchone()
        if row:
            probes = json.loads(row[0])
            probes[path] = probe
            db.execute("UPDATE import_job SET probes = ? WHERE job_id = ?", (json.dumps(probes), job_id))


def enqueue(database, image_root, groups, scan):
    library = str(Path(database).resolve())
    image_root = str(Path(image_root).resolve())
    ids, now = [], time.time()
    with closing(_connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        snapshot = {key: scan[key] for key in ("roots", "root", "records", "ignored", "warnings")}
        db.execute("INSERT OR REPLACE INTO scan_snapshot (id, payload, finished_at) VALUES (1, ?, ?)", (json.dumps(snapshot, ensure_ascii=False), now))
        for title_id, group in groups.items():
            # Multiple versions of an episode belong to different atomic chunks.
            chunks = []
            for item in sorted(group["items"], key=lambda item: item["path"]):
                position = (item["season_number"], item["episode_number"])
                chunk = next((part for part in chunks if len(part) < 100 and (item["episode_number"] in (None, "") or all((row["season_number"], row["episode_number"]) != position for row in part))), None)
                if chunk is None:
                    chunk = []
                    chunks.append(chunk)
                    if len(ids) + len(chunks) > 1000:
                        raise ValueError("单次最多提交 1000 个独立批次，请分次关联。")
                chunk.append(item)
            for chunk in chunks:
                payload = {"title_id": title_id, "items": sorted(chunk, key=lambda item: item["path"]),
                           "sources": sorted(set(group["sources"])),
                           "versions": {item["path"]: group["versions"][item["path"]] for item in chunk}}
                key = hashlib.sha256(json.dumps([library, image_root, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                existing = db.execute("SELECT * FROM import_job WHERE request_key = ?", (key,)).fetchone()
                if existing:
                    ids.append(existing["job_id"])
                    if existing["state"] in ("failed", "cancelled"):
                        db.execute("UPDATE import_job SET state = 'queued', attempts = 0, error = '', next_at = 0 WHERE job_id = ?", (existing["job_id"],))
                    else:
                        continue
                    job_id = existing["job_id"]
                else:
                    job_id = uuid.uuid4().hex
                    db.execute("INSERT INTO import_job (job_id, request_key, library_path, image_root, title_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (job_id, key, library, image_root, title_id, json.dumps(payload, ensure_ascii=False), now, now))
                    ids.append(job_id)
                for item in chunk:
                    reserved = db.execute("SELECT job_id FROM import_reservation WHERE library_path = ? AND path = ?", (library, item["path"])).fetchone()
                    if reserved and reserved["job_id"] != job_id:
                        raise ValueError("部分文件已在关联队列中，请等待当前任务完成。")
                    db.execute("INSERT OR REPLACE INTO import_reservation VALUES (?, ?, ?)", (library, item["path"], job_id))
    return ids


def cancel_title(database, title_id):
    """Called under the publication lock before deleting a work."""
    with closing(_connect(database)) as db, db:
        db.execute("UPDATE import_job SET state = 'cancelled', owner = '', error = '作品已删除，关联任务已取消。' WHERE library_path = ? AND title_id = ? AND state IN ('queued', 'running')", (str(Path(database).resolve()), title_id))
        db.execute("DELETE FROM import_reservation WHERE job_id IN (SELECT job_id FROM import_job WHERE state = 'cancelled')")


def cancel(database, ids):
    if not isinstance(ids, list) or not ids or len(ids) > 1000 or any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{32}", item) for item in ids):
        raise ValueError("任务 ID 无效。")
    with closing(_connect(database)) as db, db:
        db.execute(f"UPDATE import_job SET state = 'cancelled', owner = '', error = '关联任务已停止。' WHERE library_path = ? AND job_id IN ({','.join('?' for _ in ids)}) AND state IN ('queued', 'running')", (str(Path(database).resolve()), *ids))
        db.execute("DELETE FROM import_reservation WHERE job_id IN (SELECT job_id FROM import_job WHERE state = 'cancelled')")
    return status(database, ids)


def owns(database, job_id, owner):
    with closing(_connect(database)) as db:
        return db.execute("SELECT 1 FROM import_job WHERE job_id = ? AND owner = ? AND state = 'running'", (job_id, owner)).fetchone() is not None


def finish(database, row, state, result, error, retry):
    with closing(_connect(database)) as db, db:
        changed = db.execute("UPDATE import_job SET state = ?, result = ?, error = ?, next_at = ?, owner = '', lease_until = 0, updated_at = ? WHERE job_id = ? AND owner = ? AND state = 'running'",
            (state, json.dumps(result, ensure_ascii=False) if result else None, error, time.time() + retry if retry else 0, time.time(), row["job_id"], row["owner"]))
        if changed.rowcount and state != "queued":
            db.execute("DELETE FROM import_reservation WHERE job_id = ?", (row["job_id"],))


def running_phases(database, owner):
    with closing(_connect(database)) as db:
        return {row["job_id"]: json.loads(row["progress"]) for row in db.execute(
            "SELECT job_id, progress FROM import_job WHERE library_path = ? AND owner LIKE ? AND state = 'running'",
            (str(Path(database).resolve()), owner + ":%"))}


def heartbeat_and_next(database, owner, active):
    with closing(_connect(database)) as db, db:
        if active:
            db.execute("UPDATE import_job SET lease_until = ? WHERE library_path = ? AND owner LIKE ? AND state = 'running'",
                       (time.time() + 120, str(Path(database).resolve()), owner + ":%"))
        return db.execute("SELECT MIN(CASE WHEN state = 'running' THEN lease_until ELSE next_at END) FROM import_job WHERE library_path = ? AND state IN ('queued', 'running')", (str(Path(database).resolve()),)).fetchone()[0]


def pending(database):
    if not store.store_path(database).is_file():
        return False
    with closing(_connect(database)) as db:
        return db.execute("SELECT 1 FROM import_job WHERE library_path = ? AND state IN ('queued', 'running') LIMIT 1", (str(Path(database).resolve()),)).fetchone() is not None
