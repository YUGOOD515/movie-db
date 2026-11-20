"""一次性全新环境启动：使用临时数据目录启动 MovieDB，无论 Ctrl+C、关终端还是停止进程都自动清空全部临时数据。

    uv run python scripts/run_fresh.py
    uv run python scripts/run_fresh.py --port 5056 --open
    uv run python scripts/run_fresh.py --keys   # 沿用仓库 .env 里的 TMDB / AI / MDBList 密钥，测试完整流程
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_PREFIX = "moviedb-fresh-"
STRIP_PREFIXES = (
    "MOVIEDB_", "FLASK_", "TMDB_", "LLM_", "MDBLIST_", "ALIST_", "CLOUD_", "MPC_",
)
STRIP_NAMES = frozenset({"PLAYER", "PLAYER_PATH", "FFPROBE_PATH", "MEDIA_IMPORT_ROOT"})
# --keys 时从仓库 .env 带进临时环境的密钥：只放进进程环境变量，不写进临时数据目录以外的地方
KEY_NAMES = ("TMDB_TOKEN", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_API", "MDBLIST_APIKEY")

_WATCHER_CODE = """
import ctypes, os, shutil, sys, time
from pathlib import Path
pid, target = int(sys.argv[1]), Path(sys.argv[2])
if os.name == "nt":
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        ctypes.windll.kernel32.CloseHandle(handle)
else:
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.3)
for _ in range(25):
    if not target.exists():
        break
    try:
        shutil.rmtree(target)
        break
    except OSError:
        time.sleep(0.1)
"""


def repo_keys(env_path: Path | None = None) -> dict[str, str]:
    """仓库 .env 里已填写的密钥（KEY_NAMES），读不到时返回空字典。"""
    from dotenv import dotenv_values

    path = env_path or ROOT / ".env"
    if not path.is_file():
        return {}
    values = dotenv_values(path)
    return {name: str(values[name]).strip() for name in KEY_NAMES if str(values.get(name) or "").strip()}


def build_clean_env(data_dir: Path, port: int, base_env: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base_env is None else base_env
    env = {
        key: value
        for key, value in source.items()
        if key not in STRIP_NAMES and not key.startswith(STRIP_PREFIXES)
    }
    env.update(
        MOVIEDB_DATA_DIR=str(data_dir),
        MOVIEDB_PORT=str(port),
        FLASK_SKIP_DOTENV="1",
        PYTHONUTF8="1",
    )
    return env


def remove_tree(target: Path, retries: int = 15, delay: float = 0.1) -> bool:
    for attempt in range(retries):
        if not target.exists():
            return True
        try:
            shutil.rmtree(target)
            return True
        except OSError:
            if attempt + 1 < retries:
                time.sleep(delay)
    return not target.exists()


def sweep_stale(temp_root: Path | None = None) -> None:
    base = temp_root or Path(tempfile.gettempdir())
    try:
        entries = list(base.glob(f"{TEMP_PREFIX}*"))
    except OSError:
        return
    for item in entries:
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)


def spawn_cleanup_watcher(pid: int, data_dir: Path) -> subprocess.Popen:
    kwargs = (
        {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    return subprocess.Popen(
        [sys.executable, "-c", _WATCHER_CODE, str(pid), str(data_dir)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **kwargs,
    )


def serve(host: str, port: int, open_browser: bool = False) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from werkzeug.serving import make_server
    import app as movie_app

    server = make_server(host, port, movie_app.app, threaded=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="以一次性空数据目录启动 MovieDB，关闭后自动删除全部测试数据。")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=5056, help="监听端口（默认 5056，避开正式库常用的 5055）")
    parser.add_argument("--open", action="store_true", help="启动后在默认浏览器打开页面")
    parser.add_argument("--keys", action="store_true", help="沿用仓库 .env 里的 TMDB、AI 和 MDBList 密钥")
    args = parser.parse_args(argv)

    sweep_stale()
    data_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    clean_env = build_clean_env(data_dir, args.port)
    if args.keys:
        clean_env.update(repo_keys())
    os.environ.clear()
    os.environ.update(clean_env)

    watcher = spawn_cleanup_watcher(os.getpid(), data_dir)
    print(f"全新临时环境：http://{args.host}:{args.port} （关闭后自动清空 {data_dir}）", flush=True)
    try:
        return serve(args.host, args.port, open_browser=args.open)
    finally:
        print(f"正在退出，临时数据目录将自动清理：{data_dir}", flush=True)
        # 进程完全退出后由独立守护小进程在全部文件句柄释放后执行删除
        _ = watcher


if __name__ == "__main__":
    raise SystemExit(main())
