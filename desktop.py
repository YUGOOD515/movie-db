"""MovieDB 桌面启动器：在本机启动服务，并用原生窗口显示界面（见 desktop_window.py）。

Windows 安装包用 runtime/python/MovieDB.exe（改名并换了图标的 pythonw.exe）运行本文件，
托盘图标常驻；macOS 由 MovieDB.app 的启动器运行，留在程序坞并提供菜单栏菜单。开发时用
`uv run --with pystray --with pywebview python desktop.py`。

界面打开方式：默认独立窗口；设置 MOVIEDB_UI=browser 或加 --browser 改用系统浏览器（旧版行为，
菜单栏 / 托盘图标常驻）；没有可用的系统网页引擎（如 Windows 未装 WebView2）时自动退回浏览器。

可选参数：--port 端口；--no-browser 只运行服务，不打开窗口或浏览器；--no-tray 不显示托盘图标
（按 Ctrl+C 退出）；--self-test 打开窗口检查页面与接口后退出（安装包 CI 用）。

已经在运行时再次打开：让正在运行的实例显示窗口（浏览器模式下打开页面）。
重启（托盘菜单或设置页）：先停止服务并释放端口，再用同样的参数启动一个新进程。
"""

from __future__ import annotations

import argparse
import importlib
import json
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
sys.path.insert(0, str(BASE_DIR))
from services import paths  # noqa: E402  只依赖标准库，数据位置要在导入应用前确定

DEFAULT_PORT = 5000
UI_WINDOW, UI_BROWSER, UI_NONE = "window", "browser", "none"
_migrated: list[str] = []


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
    """没有控制台（pythonw 或 macOS 的 .app）：把输出写入 logs/moviedb.log（超过 5MB 时轮换一次）。"""
    windowless = (sys.stdout is None or Path(sys.executable).name.lower() in ("pythonw.exe", "moviedb.exe")
                  or (paths.PACKAGED and not sys.stdout.isatty()))
    if not windowless:
        for stream in (sys.stdout, sys.stderr):  # 输出被重定向时 Windows 默认编码不是 UTF-8
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        return
    log_path = paths.LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
        log_path.replace(log_path.with_suffix(".log.1"))
    stream = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = stream


def _alert(message: str) -> None:
    print(message, file=sys.stderr)
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "MovieDB", 0x40)
    elif sys.platform == "darwin":
        script = f'display alert "MovieDB" message {_applescript(message)}'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _running_instance(url: str) -> bool | None:
    """True：本程序已在该端口运行；False：端口被其他程序占用；None：端口空闲。"""
    try:
        with urllib.request.urlopen(url + "/setup/status", timeout=2) as response:
            return response.headers.get("X-MovieDB-Origin") == "local"
    except urllib.error.HTTPError as error:
        return error.headers.get("X-MovieDB-Origin") == "local"
    except (urllib.error.URLError, OSError):
        return None


def _locate_data() -> None:
    """确定数据目录；旧版安装把数据放在程序目录里，首次启动新版时搬到数据目录。"""
    global paths
    if not paths.PACKAGED or paths.PORTABLE or os.environ.get("MOVIEDB_DATA_DIR"):
        return
    try:
        _migrated[:] = paths.migrate_legacy()
    except OSError:
        # 搬不动（文件被占用等）：这次继续用旧位置，下次启动再试
        os.environ["MOVIEDB_DATA_DIR"] = str(paths.APP_DIR)
        paths = importlib.reload(paths)


def _prepare_environment() -> None:
    from dotenv import load_dotenv

    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(BASE_DIR)
    load_dotenv(paths.ENV_PATH)
    if paths.BUNDLED_FFPROBE.is_file() and os.environ.get("FFPROBE_PATH", "ffprobe").strip() in ("", "ffprobe"):
        os.environ["FFPROBE_PATH"] = str(paths.BUNDLED_FFPROBE)


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 — 打开资源管理器
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # 在访达中打开
    else:
        subprocess.Popen(["xdg-open", str(path)])


def relaunch_command(argv: list[str]) -> list[str]:
    """重启用的命令：沿用原参数并加上 --restarted（窗口照常打开，浏览器模式不再新开页面，设置页会自己刷新）。"""
    args = [arg for arg in argv if arg not in ("--restarted", "--self-test")] + ["--restarted"]
    launcher = paths.APP_DIR.parent.parent / "MacOS" / "MovieDB"
    if sys.platform == "darwin" and paths.PACKAGED and launcher.is_file():
        return [str(launcher), *args]  # 经 .app 的启动器重新运行，环境与双击打开时一致
    # 沿用解释器参数（macOS 启动器用 -B -s 运行，避免往 .app 里写字节码）
    flags = subprocess._args_from_interpreter_flags()  # noqa: SLF001 — 标准库多进程也用它
    return [sys.executable, *flags, str(BASE_DIR / "desktop.py"), *args]


def ui_mode(args: argparse.Namespace, environ=os.environ) -> str:
    """window：原生窗口；browser：系统浏览器 + 托盘；none：只运行服务。"""
    if args.self_test:
        return UI_WINDOW
    if args.no_browser:
        return UI_NONE
    if args.browser or environ.get("MOVIEDB_UI", "").strip().lower() == UI_BROWSER:
        return UI_BROWSER
    return UI_WINDOW


def _show_running(url: str) -> bool:
    """请已运行的实例显示窗口；它在浏览器模式或没有响应时返回 False。"""
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # 允许它把窗口带到前台
    request = urllib.request.Request(url + "/desktop/show", data=b"", method="POST",
                                     headers={"X-MovieDB-Desktop": "1"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return bool(json.load(response).get("shown"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _spawn(command: list[str]) -> None:
    options = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True  # 与当前进程脱离，旧进程退出后继续运行
    subprocess.Popen(command, cwd=BASE_DIR, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)


def _configured_port(cli_port: int | None) -> int:
    try:
        return cli_port or int(os.environ.get("MOVIEDB_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def _tray_icon(url: str, open_app, open_settings, restart, quit_app):
    """托盘 / 菜单栏图标；没装 pystray 或当前系统不支持托盘时返回 None。"""
    try:
        import pystray
    except Exception:  # noqa: BLE001
        return None

    def quit_item(icon, item):
        icon.visible = False
        quit_app(icon)

    menu = pystray.Menu(
        pystray.MenuItem("打开 MovieDB", lambda icon, item: open_app(), default=True),
        pystray.MenuItem("打开设置", lambda icon, item: open_settings()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开数据文件夹", lambda icon, item: _open_folder(paths.DATA_DIR)),
        pystray.MenuItem("查看日志", lambda icon, item: _open_folder(paths.LOG_PATH.parent)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("重启 MovieDB", lambda icon, item: restart()),
        pystray.MenuItem("退出", quit_item),
    )
    try:
        return pystray.Icon("MovieDB", make_icon(64), f"MovieDB · {url}", menu)
    except Exception as error:  # noqa: BLE001
        print(f"无法创建托盘图标：{error}", flush=True)
        return None


def _menu_bar_only(on_reopen) -> object | None:
    """macOS 浏览器模式：只在菜单栏显示图标；在访达里再次打开 MovieDB 时打开页面。"""
    try:
        import AppKit
        import Foundation
    except ImportError:
        return None

    class ReopenDelegate(AppKit.NSObject):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
            on_reopen()
            return Foundation.NO

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    delegate = ReopenDelegate.alloc().init()
    app.setDelegate_(delegate)
    return delegate  # 调用方要持有引用，NSApplication 不保留委托


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MovieDB 桌面启动器")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--browser", action="store_true", help="用系统浏览器打开界面")
    parser.add_argument("--no-browser", action="store_true", help="只运行服务，不打开窗口或浏览器")
    parser.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
    parser.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    _locate_data()
    _redirect_output()
    _prepare_environment()
    if _migrated:
        _log(f"已把数据从 {paths.APP_DIR} 迁移到 {paths.DATA_DIR}：" + "、".join(_migrated))
    port = _configured_port(args.port)
    url = f"http://127.0.0.1:{port}"
    mode = ui_mode(args)

    running = _running_instance(url)
    if running:
        # 已经在运行：再次打开只把界面带到前台
        if mode != UI_NONE and not args.restarted and not _show_running(url):
            webbrowser.open(url + "/")
        return 0
    if running is False:
        _alert(f"端口 {port} 已被其他程序占用。可在 {paths.ENV_PATH} 中设置 MOVIEDB_PORT 换一个端口。")
        return 1

    if mode == UI_WINDOW:
        import desktop_window

        reason = desktop_window.unavailable_reason()
        if reason:
            _log(f"无法使用独立窗口，改用系统浏览器：{reason}")
            if args.self_test:
                print(f"窗口自检失败：{reason}", flush=True)
                return 1
            mode = UI_BROWSER

    _log(f"MovieDB 启动：{url}（界面 {mode}，程序 {BASE_DIR}，数据 {paths.DATA_DIR}）")
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

    stopped = threading.Event()
    restarting = threading.Event()
    ui: dict = {}

    def stop(*_):
        if stopped.is_set():
            return
        stopped.set()
        server.shutdown()
        icon = ui.get("icon")
        if icon is not None:
            icon.visible = False
            icon.stop()
        shell = ui.get("shell")
        if shell is not None:
            shell.quit()

    def request_restart() -> str:
        """设置页与托盘共用：稍后停止服务（让当前请求先返回），退出后由新进程接手。"""
        restarting.set()
        threading.Timer(0.5, stop).start()
        return f"http://127.0.0.1:{_configured_port(args.port)}"

    app.config["RESTART_HANDLER"] = request_restart

    def open_browser(path: str = "/"):
        webbrowser.open(url + path)

    if mode == UI_WINDOW:
        _run_window(args, url, ui, stop, request_restart, app)
    else:
        if mode == UI_BROWSER and not args.restarted:
            threading.Timer(0.6, open_browser).start()
        icon = None if args.no_tray else _tray_icon(
            url, open_browser, lambda: open_browser("/settings"), request_restart, stop)
        if icon is not None:
            ui["icon"] = icon
            if sys.platform == "darwin":
                ui["delegate"] = _menu_bar_only(open_browser)
            icon.run()  # 阻塞到选择“退出”或重启
        else:
            try:
                while thread.is_alive():
                    thread.join(0.5)
            except KeyboardInterrupt:
                pass
    stop()
    thread.join(5)
    server.server_close()  # 释放端口，新进程才能用同一端口启动
    if args.self_test:
        return 0 if ui["shell"].self_test_ok else 1
    if restarting.is_set():
        _log("MovieDB 正在重启")
        _spawn(relaunch_command(sys.argv[1:] if argv is None else argv))
        return 0
    _log("MovieDB 已退出")
    return 0


def _run_window(args, url: str, ui: dict, stop, restart, app) -> None:
    """原生窗口跑在主线程，直到退出；Windows 的托盘图标在后台线程。"""
    from desktop_window import DesktopWindow

    actions = {
        "open_data": lambda: _open_folder(paths.DATA_DIR),
        "open_logs": lambda: _open_folder(paths.LOG_PATH.parent),
        "restart": restart,
    }
    icon = None
    if os.name == "nt" and not args.no_tray and not args.self_test:
        icon = _tray_icon(url, lambda: shell.show(), lambda: shell.open("/settings"), restart, stop)
    if icon is not None:
        actions["notify_background"] = lambda message: icon.notify(message, "MovieDB")
    # macOS 关窗后留在程序坞；Windows 有托盘图标才在关窗后继续后台运行
    shell = DesktopWindow(url, actions=actions, self_test=args.self_test,
                          hide_on_close=sys.platform == "darwin" or icon is not None)
    ui["shell"] = shell
    app.config["SHOW_WINDOW_HANDLER"] = shell.show
    if icon is not None:
        ui["icon"] = icon
        threading.Thread(target=icon.run, name="moviedb-tray", daemon=True).start()
    shell.run(show=True)


if __name__ == "__main__":
    sys.exit(main())
