"""MovieDB 桌面启动器：在本机启动服务、打开浏览器，并在系统托盘常驻。

安装包用 runtime/python/pythonw.exe 运行本文件（没有控制台窗口）；开发时也可以
`uv run python desktop.py`。可选参数：--port 端口、--no-browser 不自动打开浏览器、
--no-tray 不显示托盘图标（按 Ctrl+C 退出）。

重启（托盘菜单或设置页）：先停止服务并释放端口，再用同样的参数启动一个新进程。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR.parent / "runtime"
BUNDLED_FFPROBE = RUNTIME_DIR / "ffmpeg" / "ffprobe.exe"
LOG_PATH = BASE_DIR / "logs" / "moviedb.log"
DEFAULT_PORT = 5000


def make_icon(size: int = 256):
    """程序图标：圆角深色底上的白色播放三角（托盘与安装包共用）。"""
    from PIL import Image, ImageDraw

    scale = 4  # 先放大绘制再缩小，边缘更平滑
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, canvas - 1, canvas - 1), radius=canvas * 0.24, fill=(24, 26, 31, 255))
    inset = canvas * 0.07
    draw.rounded_rectangle((inset, inset, canvas - inset, canvas - inset), radius=canvas * 0.19,
                           outline=(255, 255, 255, 40), width=max(1, int(canvas * 0.012)))
    left, top, bottom, right = canvas * 0.37, canvas * 0.28, canvas * 0.72, canvas * 0.73
    draw.polygon([(left, top), (right, canvas / 2), (left, bottom)], fill=(255, 255, 255, 255))
    return image.resize((size, size), Image.LANCZOS)


def _redirect_output() -> None:
    """pythonw 没有控制台：把输出写入 logs/moviedb.log（超过 5MB 时轮换一次）。"""
    windowless = sys.stdout is None or Path(sys.executable).name.lower() == "pythonw.exe"
    if not windowless:
        for stream in (sys.stdout, sys.stderr):  # 输出被重定向时 Windows 默认编码不是 UTF-8
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 5 * 1024 * 1024:
        LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
    stream = open(LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = stream


def _alert(message: str) -> None:
    print(message, file=sys.stderr)
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "MovieDB", 0x40)


def _running_instance(url: str) -> bool | None:
    """True：本程序已在该端口运行；False：端口被其他程序占用；None：端口空闲。"""
    try:
        with urllib.request.urlopen(url + "/setup/status", timeout=2) as response:
            return response.headers.get("X-MovieDB-Origin") == "local"
    except urllib.error.HTTPError as error:
        return error.headers.get("X-MovieDB-Origin") == "local"
    except (urllib.error.URLError, OSError):
        return None


def _prepare_environment() -> None:
    from dotenv import load_dotenv

    os.chdir(BASE_DIR)
    load_dotenv(BASE_DIR / ".env")
    if BUNDLED_FFPROBE.is_file() and os.environ.get("FFPROBE_PATH", "ffprobe").strip() in ("", "ffprobe"):
        os.environ["FFPROBE_PATH"] = str(BUNDLED_FFPROBE)


def _open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 — 打开资源管理器
    else:
        webbrowser.open(path.as_uri())


def relaunch_command(argv: list[str]) -> list[str]:
    """重启用的命令：沿用原参数，不再自动打开浏览器（设置页会自己跳转）。"""
    args = [arg for arg in argv if arg != "--no-browser"]
    return [sys.executable, str(BASE_DIR / "desktop.py"), *args, "--no-browser"]


def _spawn(command: list[str]) -> None:
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command, cwd=BASE_DIR, close_fds=True, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _configured_port(cli_port: int | None) -> int:
    try:
        return cli_port or int(os.environ.get("MOVIEDB_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def _run_tray(url: str, stop, restart=None, holder: dict | None = None) -> bool:
    try:
        import pystray
    except Exception:  # 没装 pystray 或当前系统不支持托盘
        return False

    def open_app(icon=None, item=None):
        webbrowser.open(url + "/")

    def quit_app(icon, item):
        icon.visible = False
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开 MovieDB", open_app, default=True),
        pystray.MenuItem("打开设置", lambda icon, item: webbrowser.open(url + "/settings")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开数据文件夹", lambda icon, item: _open_folder(BASE_DIR)),
        pystray.MenuItem("查看日志", lambda icon, item: _open_folder(LOG_PATH.parent)),
        pystray.Menu.SEPARATOR,
        *([pystray.MenuItem("重启 MovieDB", lambda icon, item: restart())] if restart else []),
        pystray.MenuItem("退出", quit_app),
    )
    icon = pystray.Icon("MovieDB", make_icon(64), f"MovieDB · {url}", menu)
    if holder is not None:
        holder["icon"] = icon
    icon.run()  # 阻塞到选择“退出”或重启
    stop()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MovieDB 桌面启动器")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    args = parser.parse_args(argv)

    _redirect_output()
    _prepare_environment()
    port = _configured_port(args.port)
    url = f"http://127.0.0.1:{port}"

    running = _running_instance(url)
    if running:
        # 已经在运行：双击快捷方式只打开页面
        if not args.no_browser:
            webbrowser.open(url + "/")
        return 0
    if running is False:
        _alert(f"端口 {port} 已被其他程序占用。可在 {BASE_DIR / '.env'} 中设置 MOVIEDB_PORT 换一个端口。")
        return 1

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] MovieDB 启动：{url}（{BASE_DIR}）", flush=True)
    from werkzeug.serving import make_server

    from app import app

    try:
        # 多线程：导入进度（NDJSON 流）与其他页面请求可以同时进行
        server = make_server("127.0.0.1", port, app, threaded=True)
    except OSError as error:
        _alert(f"无法在端口 {port} 启动 MovieDB：{error}")
        return 1
    thread = threading.Thread(target=server.serve_forever, name="moviedb-server", daemon=True)
    thread.start()
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url + "/",)).start()

    stopped = threading.Event()
    restarting = threading.Event()
    tray: dict = {}

    def stop():
        if not stopped.is_set():
            stopped.set()
            server.shutdown()
            icon = tray.get("icon")
            if icon is not None:
                icon.visible = False
                icon.stop()

    def request_restart() -> str:
        """设置页与托盘共用：稍后停止服务（让当前请求先返回），退出后由新进程接手。"""
        restarting.set()
        threading.Timer(0.5, stop).start()
        return f"http://127.0.0.1:{_configured_port(args.port)}"

    app.config["RESTART_HANDLER"] = request_restart

    if args.no_tray or not _run_tray(url, stop, request_restart, tray):
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            stop()
    thread.join(5)
    server.server_close()  # 释放端口，新进程才能用同一端口启动
    if restarting.is_set():
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] MovieDB 正在重启", flush=True)
        _spawn(relaunch_command(sys.argv[1:] if argv is None else argv))
        return 0
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] MovieDB 已退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
