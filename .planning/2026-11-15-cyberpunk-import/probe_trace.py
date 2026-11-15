"""复现赛博朋克任务的 ffprobe 超时：取第 N 集的 /stream 地址，带 MovieDB-Probe 标识跑 ffprobe，逐行打印耗时（签名打码）。

    uv run python .planning/2026-11-15-cyberpunk-import/probe_trace.py [集序号=1] [超时秒=90] [额外 ffprobe 参数...]
"""
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from connectors import media_probe  # noqa: E402
from services import media_source  # noqa: E402

index = int(sys.argv[1]) if len(sys.argv) > 1 else 1
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 90
extra = sys.argv[3:]
db = sqlite3.connect(ROOT / "local.db")
payload = json.loads(db.execute("SELECT payload FROM import_job WHERE job_id LIKE '16a1d84b%'").fetchone()[0])
path = payload["items"][index - 1]["path"]
url = media_source.stream_url(path)
mask = lambda text: re.sub(r"/stream/[^/\s]+", "/stream/<签名>", text)
print("文件：", path.rsplit("/", 1)[-1])

command = [media_probe.executable(), "-v", "debug", *extra, "-show_format", "-show_streams", "-of", "json",
           "-user_agent", "MovieDB-Probe", url]
start = time.monotonic()
process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace")
threading.Timer(limit, process.kill).start()
for line in process.stderr:
    if re.search(r"request:|Range|Seek|seek|HTTP error|Server returned|Opening|Input #|Stream #|probesize|Statistics|error", line, re.I):
        print(f"{time.monotonic() - start:6.1f}s  {mask(line.rstrip())[:160]}")
process.wait()
print(f"结束：{time.monotonic() - start:.1f}s，退出码 {process.returncode}")
