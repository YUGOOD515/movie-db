"""可重建的 IMDb 单集索引；只保存四整数列，发布时原子替换。"""

from contextlib import closing
import csv
import gzip
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock

_lock = RLock()
_metadata = {}
_lookups = {}
SCHEMA = """
CREATE TABLE episode (
    parent INTEGER NOT NULL,
    season INTEGER NOT NULL,
    number INTEGER NOT NULL,
    title INTEGER NOT NULL,
    PRIMARY KEY (parent, season, number, title)
) WITHOUT ROWID;
CREATE TABLE source (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL);
"""


def _read(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def info(path):
    path = Path(path)
    with _lock:
        try:
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            cached = _metadata.get(str(path))
            if cached and cached[0] == signature:
                return dict(cached[1])
            with closing(_read(path)) as db:
                row = db.execute("SELECT payload FROM source WHERE id = 1").fetchone()
            result = json.loads(row[0])
            if not isinstance(result, dict) or result.get("schema") != 1 or not result.get("rows") or not result.get("revision"):
                return None
            result["size_bytes"] = stat.st_size
            _metadata[str(path)] = (signature, result)
            return dict(result)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None


def lookup(path, parent):
    """只返回有唯一 IMDb 单集 ID 的季集；未知编号和重复候选不自动采用。"""
    if not re.fullmatch(r"tt[0-9]+", str(parent)):
        return {}
    with _lock:
        metadata = info(path)
        if not metadata:
            return {}
        key = (str(path), metadata["revision"], parent)
        if key in _lookups:
            return dict(_lookups[key])
        try:
            with closing(_read(path)) as db:
                rows = db.execute("""
                    SELECT season, number, title
                      FROM episode
                     WHERE parent = ? AND season >= 0 AND number > 0
                """, (int(parent[2:]),)).fetchall()
        except (OSError, sqlite3.Error):
            return {}
        matches = {}
        for season, number, title in rows:
            matches.setdefault((season, number), set()).add(f"tt{title:07d}")
        found = {position: next(iter(ids)) for position, ids in matches.items() if len(ids) == 1}
        if len(_lookups) >= 256:
            _lookups.clear()
        _lookups[key] = found
        return dict(found)


def build(source, target, metadata, progress=None):
    """调用方提供新的临时目标；坏 gzip/格式/数据不能成为可用索引。"""
    source, target = Path(source), Path(target)
    if target.exists():
        raise ValueError("临时索引已存在。")
    rows, batch = 0, []
    with closing(sqlite3.connect(target)) as db:
        # 仅用于全新、可丢弃的临时索引，完整检查后才发布。
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.execute("PRAGMA cache_size = -65536")
        db.executescript(SCHEMA)
        with gzip.open(source, "rt", encoding="utf-8", newline="") as file:
            reader = csv.reader(file, delimiter="\t")
            if next(reader, None) != ["tconst", "parentTconst", "seasonNumber", "episodeNumber"]:
                raise ValueError("IMDb 单集文件格式无法识别。")
            db.execute("BEGIN")
            for episode, parent, season, number in reader:
                if not re.fullmatch(r"tt\d+", episode) or not re.fullmatch(r"tt\d+", parent):
                    raise ValueError("IMDb 单集编号无效。")
                s, e = (-1 if value == r"\N" else int(value) for value in (season, number))
                if s < -1 or e < -1:
                    raise ValueError("IMDb 季集编号无效。")
                batch.append((int(parent[2:]), s, e, int(episode[2:])))
                rows += 1
                if len(batch) >= 25000:
                    db.executemany("INSERT INTO episode VALUES (?, ?, ?, ?)", batch)
                    batch.clear()
                    if progress:
                        progress(rows)
            db.executemany("INSERT INTO episode VALUES (?, ?, ?, ?)", batch)
            if not rows:
                raise ValueError("IMDb 单集文件为空。")
            payload = {**metadata, "schema": 1, "rows": rows}
            db.execute("INSERT INTO source VALUES (1, ?)", (json.dumps(payload),))
            db.commit()
        db.execute("VACUUM")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("IMDb 单集索引校验失败。")
    return rows


def publish(temporary, path):
    with _lock:
        Path(temporary).replace(path)
        _metadata.pop(str(path), None)
        _lookups.clear()


def checked(path, timestamp):
    with _lock:
        metadata = info(path)
        if not metadata:
            raise ValueError("本地单集库不可用，请重新更新。")
        metadata.pop("size_bytes", None)
        metadata["checked_at"] = timestamp
        with closing(sqlite3.connect(path, timeout=2)) as db, db:
            db.execute("UPDATE source SET payload = ? WHERE id = 1", (json.dumps(metadata),))
        _metadata.pop(str(path), None)
