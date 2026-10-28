"""Bounded import observations, coalesced to discovery.db; checkpoints stay separate."""
from copy import deepcopy
from contextlib import closing
import json
import logging
import threading
import time

from connectors import events
from models import import_store

_lock = threading.RLock()
_live = {}
logger = logging.getLogger(__name__)


def snapshots():
    with _lock:
        reporters = list(_live.items())
    return {key: reporter.snapshot() for key, reporter in reporters}


class Reporter:
    def __init__(self, database, row):
        self.database, self.row = database, row
        self.lock = threading.RLock()
        self.data = json.loads(row.get("progress") or "{}")
        # Per-attempt stages start afresh; durable core/probe checkpoints remain.
        self.data.update(stages={}, active=[], issues=[], waits={})
        self.active = {}
        self.last_write = 0

    def snapshot(self):
        with self.lock:
            return deepcopy(self.data)

    def _save(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_write < .75:
            return
        self.last_write = now
        try:
            with closing(import_store._connect(self.database)) as db, db:
                db.execute("UPDATE import_job SET progress = ?, updated_at = ? WHERE job_id = ? AND owner = ? AND state = 'running'",
                           (json.dumps(self.data, ensure_ascii=False), time.time(), self.row["job_id"], self.row["owner"]))
        except Exception:
            # UI telemetry must not turn a successful import into a failed job.
            logger.warning("Could not persist import progress", exc_info=True)

    def progress(self, phase, done, total):
        with self.lock:
            self.data.update(phase=phase, total=total)
            self.data[phase] = done
            if phase == "enrich":
                self.data["core"] = done
            self._save(force=phase in ("write", "enrich"))

    def event(self, kind, **item):
        with self.lock:
            stage = item.get("stage")
            if stage:
                counts = self.data["stages"].setdefault(stage, {"done": 0, "total": 0, "failed": 0})
            if kind == "plan":
                counts["total"] += item["total"]
            elif kind == "start":
                self.active[(stage, item["key"])] = {"stage": stage, "label": item["label"]}
            elif kind == "end":
                self.active.pop((stage, item["key"]), None)
                counts["done"] += 1
                counts["failed"] += bool(item.get("failed"))
            elif kind == "title":
                self.data["name"] = events.safe_text(item["name"], 120)
            elif kind == "issue":
                issue = {key: events.safe_text(item.get(key), 180) for key in ("stage", "service", "code", "reason", "label")}
                if issue not in self.data["issues"]:
                    self.data["issues"] = (self.data["issues"] + [issue])[-8:]
            elif kind == "wait":
                self.data["waits"][item["service"]] = {key: item.get(key) for key in ("until", "code", "reason")}
            elif kind == "service_ok":
                self.data["waits"].pop(item["service"], None)
            self.data["active"] = list(self.active.values())[:24]
            self._save(force=kind in ("title", "issue", "wait"))

    def finish(self):
        with self.lock:
            self.data["active"] = []
            self.active.clear()
            self._save(force=True)

    def __enter__(self):
        self.context = events.observe(self.event)
        self.context.__enter__()
        with _lock:
            _live[self.row["job_id"]] = self
        return self

    def __exit__(self, *error):
        self.context.__exit__(*error)
        with _lock:
            if _live.get(self.row["job_id"]) is self:
                _live.pop(self.row["job_id"], None)
