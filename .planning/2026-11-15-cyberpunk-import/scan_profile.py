"""只读：按扫描的方式把扫描根目录列一遍（不走缓存），统计夸克列目录的请求数、耗时，以及各顶层目录是否值得扫。

    uv run python -X utf8 .planning/2026-11-15-cyberpunk-import/scan_profile.py
"""
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from connectors.drives import quark  # noqa: E402
from services import media_source  # noqa: E402

calls, lock = [], threading.Lock()
original = quark.QuarkDrive._request


def timed(self, method, path, **kwargs):
    start = time.perf_counter()
    try:
        return original(self, method, path, **kwargs)
    finally:
        with lock:
            calls.append((path, time.perf_counter() - start, threading.current_thread().name))


quark.QuarkDrive._request = timed

root = sqlite3.connect(ROOT / "local.db").execute("SELECT path FROM scan_root").fetchone()[0]
linked = {row[0] for row in sqlite3.connect(ROOT / "library.db").execute("SELECT alist_path FROM media_file")}
started = time.perf_counter()
top = media_source.list_directory(root, refresh=True)


def walk(path):
    """返回 (目录数, 文件数, 视频路径列表)。"""
    dirs, files, videos = 1, 0, []
    for entry in media_source.list_directory(path, refresh=True):
        child = str(PurePosixPath(path) / entry["name"])
        if entry.get("is_dir"):
            d, f, v = walk(child)
            dirs, files, videos = dirs + d, files + f, videos + v
        else:
            files += 1
            if PurePosixPath(child).suffix.lower() in media_source.VIDEO_EXTENSIONS:
                videos.append(child)
    return dirs, files, videos


def measure(entry):
    path = str(PurePosixPath(root) / entry["name"])
    start = time.perf_counter()
    if entry.get("is_dir"):
        dirs, files, videos = walk(path)
    else:
        dirs, files, videos = 0, 1, [path] if PurePosixPath(path).suffix.lower() in media_source.VIDEO_EXTENSIONS else []
    pending = [v for v in videos if v not in linked]
    kind = "无视频" if not videos else "全部已关联" if not pending else "有待关联"
    return entry["name"], time.perf_counter() - start, dirs, files, len(videos), len(pending), kind


with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(measure, top))
total = time.perf_counter() - started

durations = sorted(d for _, d, _ in calls)
print(f"总耗时 {total:.1f}s；夸克请求 {len(calls)} 次，平均 {sum(durations) / len(durations):.2f}s，"
      f"中位 {durations[len(durations) // 2]:.2f}s，P90 {durations[int(len(durations) * .9)]:.2f}s，最慢 {durations[-1]:.2f}s")
by_kind = defaultdict(lambda: [0, 0.0, 0, 0])
for name, seconds, dirs, files, videos, pending, kind in results:
    k = by_kind[kind]
    k[0] += 1; k[1] += seconds; k[2] += dirs; k[3] += videos
for kind, (count, seconds, dirs, videos) in by_kind.items():
    print(f"{kind}：{count} 个顶层条目，{dirs} 个目录，{videos} 个视频，累计 {seconds:.1f}s")
print("最慢的 10 个顶层条目：")
for name, seconds, dirs, files, videos, pending, kind in sorted(results, key=lambda r: -r[1])[:10]:
    print(f"  {seconds:6.1f}s  {dirs:4d} 目录 {files:5d} 文件 {videos:4d} 视频 待关联 {pending:3d}  {kind}  {name[:40]}")
