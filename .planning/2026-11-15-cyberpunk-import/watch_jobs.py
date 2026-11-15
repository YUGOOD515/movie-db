"""监视指定作品的关联任务：每 2 秒读 local.db，状态 / 阶段 / 计数变化时打一行带耗时的日志，全部结束（或超时）退出。

    uv run python .planning/2026-11-15-cyberpunk-import/watch_jobs.py <开始时间戳> <最长秒数> tt... tt...
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
since, limit, ids = float(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]
start, last = time.monotonic(), {}
while time.monotonic() - start < limit:
    db = sqlite3.connect(f"file:{ROOT / 'local.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(f"SELECT * FROM import_job WHERE created_at >= ? AND title_id IN ({','.join('?' for _ in ids)})",
                      (since, *ids)).fetchall()
    db.close()
    for row in rows:
        progress = json.loads(row["progress"] or "{}")
        stages = {k: f"{v.get('done')}/{v.get('total')}" + (f"!{v['failed']}" if v.get("failed") else "")
                  for k, v in progress.get("stages", {}).items()}
        state = (row["state"], row["attempts"], progress.get("phase"), progress.get("core"), json.dumps(stages, ensure_ascii=False),
                 row["error"][:80])
        if last.get(row["job_id"]) != state:
            print(f"{time.monotonic() - start:6.1f}s  {progress.get('name') or row['title_id']:<10} {row['state']:<9} 第{row['attempts']}轮 "
                  f"阶段={progress.get('phase')} 写入={progress.get('core')}/{progress.get('total')} {state[4]} {row['error'][:80]}", flush=True)
            last[row["job_id"]] = state
    if rows and len(rows) >= len(ids) and all(r["state"] in ("complete", "failed", "cancelled") for r in rows):
        break
    time.sleep(2)
print(f"结束 {time.monotonic() - start:.1f}s", flush=True)
