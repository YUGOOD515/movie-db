"""每 2 秒查一次 /manager/discovery，状态或计数变化时打一行带耗时的日志，扫描结束（或超时）退出。

    uv run python .planning/2026-11-15-cyberpunk-import/watch_scan.py [最长秒数=900]
"""
import json
import sys
import time
import urllib.request

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 900
keys = ("status", "done", "total", "rule_done", "to_recognize", "recognized", "verifying", "ai_pending", "missing")
start, last, seen_running = time.monotonic(), None, False
while time.monotonic() - start < limit:
    data = json.load(urllib.request.urlopen("http://127.0.0.1:5000/manager/discovery"))
    snapshot = tuple(data.get(key) for key in keys)
    if snapshot != last:
        names = [r["name"][:28] for r in data.get("records", [])]
        print(f"{time.monotonic() - start:6.1f}s  " + " ".join(f"{k}={v}" for k, v in zip(keys, snapshot))
              + f"  records={len(names)} {data.get('ai_summary') or ''}", flush=True)
        last = snapshot
    seen_running = seen_running or data.get("status") not in ("complete", "idle", None)
    if seen_running and data.get("status") in ("complete", "idle"):
        break
    time.sleep(2)
print(f"结束 {time.monotonic() - start:.1f}s", flush=True)
