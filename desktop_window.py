"""MovieDB 的原生窗口：用系统自带的网页引擎显示界面（Windows 为 WebView2，macOS 为 WKWebView）。

pywebview 只是一层很薄的绑定，安装包不附带浏览器内核。窗口与平台的习惯保持一致：

- 关闭窗口只是隐藏，服务继续在后台运行（扫描、同步不中断）。Windows 从托盘图标重新打开；
  macOS 留在程序坞，点程序坞图标或再次打开 MovieDB 即可，⌘Q 退出。
- 记住窗口位置与大小；标题栏随页面的浅色 / 深色主题切换。
- 外部链接（TMDB、IMDb 等）用系统浏览器打开；设置页的程序路径可用系统的文件选择框选择。
- macOS 菜单栏提供“设置…（⌘,）”“返回 / 前进（⌘[ / ⌘]）”“重新载入（⌘R）”等菜单项。

本模块只在窗口模式下导入 pywebview；几何与状态处理是纯函数，便于测试。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from services import paths

logger = logging.getLogger("moviedb.window")

STATE_PATH = paths.DATA_DIR / "window.json"
DEFAULT_SIZE = (1440, 900)
MIN_SIZE = (960, 600)
BACKGROUND = {"dark": "#212529", "light": "#ffffff"}  # 与 Bootstrap 的页面底色一致，避免打开时闪白
LOCALIZATION = {
    "global.quitConfirmation": "确定要退出吗？",
    "global.ok": "好",
    "global.quit": "退出",
    "global.cancel": "取消",
    "global.saveFile": "保存文件",
    "cocoa.menu.about": "关于",
    "cocoa.menu.services": "服务",
    "cocoa.menu.view": "显示",
    "cocoa.menu.edit": "编辑",
    "cocoa.menu.hide": "隐藏",
    "cocoa.menu.hideOthers": "隐藏其他",
    "cocoa.menu.showAll": "全部显示",
    "cocoa.menu.quit": "退出",
    "cocoa.menu.fullscreen": "进入全屏幕",
    "cocoa.menu.cut": "剪切",
    "cocoa.menu.copy": "拷贝",
    "cocoa.menu.paste": "粘贴",
    "cocoa.menu.selectAll": "全选",
    "windows.fileFilter.allFiles": "所有文件",
    "windows.fileFilter.otherFiles": "其他文件",
}
MAC_SHORTCUTS = {"设置…": ",", "返回": "[", "前进": "]", "重新载入": "r"}


# --------------------------------------------------------------------------
# 状态与几何（纯函数）
# --------------------------------------------------------------------------

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        logger.warning("保存窗口状态失败：%s", error)


def initial_geometry(state: dict) -> dict:
    """create_window 的尺寸与位置参数；没有记录或记录不合理时用默认大小并居中。"""
    def number(key):
        value = state.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    width, height, x, y = number("width"), number("height"), number("x"), number("y")
    if not width or not height or width < MIN_SIZE[0] or height < MIN_SIZE[1]:
        return {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1], "x": None, "y": None}
    return {"width": width, "height": height, "x": x, "y": y}


def fit_to_screens(window_rect: tuple[int, int, int, int],
                   screens: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    """窗口大部分落在屏幕外（例如外接显示器已拔掉）或比屏幕还大时，返回放进主屏幕的新位置；否则 None。

    矩形都是 (x, y, 宽, 高)。
    """
    if not screens:
        return None
    x, y, width, height = window_rect

    def overlap(screen):
        sx, sy, sw, sh = screen
        return max(0, min(x + width, sx + sw) - max(x, sx)) * max(0, min(y + height, sy + sh) - max(y, sy))

    best = max(screens, key=overlap)
    sx, sy, sw, sh = best
    visible = overlap(best) >= 0.5 * width * height
    fits = width <= sw and height <= sh
    if visible and fits:
        return None
    target = best if visible else screens[0]
    sx, sy, sw, sh = target
    new_width, new_height = min(width, int(sw * 0.9)), min(height, int(sh * 0.9))
    return (sx + (sw - new_width) // 2, sy + (sh - new_height) // 2, new_width, new_height)


def is_external(url: str, origin: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and f"{parsed.scheme}://{parsed.netloc}" != origin


# --------------------------------------------------------------------------
# 窗口
# --------------------------------------------------------------------------

def unavailable_reason() -> str | None:
    """不能使用原生窗口的原因；可以使用时返回 None。"""
    try:
        import webview  # noqa: F401
    except Exception as error:  # noqa: BLE001 — 任何导入问题都退回浏览器
        return f"没有可用的 pywebview（{error}）"
    if os.name == "nt":
        try:
            from webview.platforms import winforms
        except Exception as error:  # noqa: BLE001 — 缺少 .NET 等
            return f"无法加载 Windows 窗口组件（{error}）"
        if not winforms.is_chromium:
            return "没有安装 Microsoft Edge WebView2 运行时"
    return None


class Api:
    """页面里通过 window.pywebview.api 调用的方法（只在 MovieDB 窗口里存在）。"""

    def __init__(self, shell: "DesktopWindow"):
        self._shell = shell

    def set_theme(self, theme):
        self._shell.apply_theme("light" if theme == "light" else "dark")
        return True

    def open_external(self, url):
        if isinstance(url, str) and urlparse(url).scheme in ("http", "https"):
            webbrowser.open(url)
            return True
        return False

    def pick_path(self, mode="file", current=""):
        return self._shell.pick_path(mode, current if isinstance(current, str) else "")


class DesktopWindow:
    def __init__(self, url: str, *, title: str = "MovieDB", actions: dict | None = None,
                 self_test: bool = False, hide_on_close: bool = True):
        self.url = url
        self.hide_on_close = hide_on_close  # Windows 没有托盘图标时，关闭窗口就退出
        self.title = title
        self.actions = actions or {}
        self.self_test = self_test
        self.self_test_ok = False
        self.state = load_state()
        self.window = None
        self._quitting = False
        self._maximized = bool(self.state.get("maximized"))
        self._minimized = False
        self._lock = threading.Lock()

    # ---- 外部调用（任意线程） ----
    def show(self) -> bool:
        window = self.window
        if window is None:
            return False
        try:
            window.show()  # Windows 上同时激活窗口（请求方已调用 AllowSetForegroundWindow）
            if self._minimized:
                window.restore()
        except Exception as error:  # noqa: BLE001
            logger.warning("显示窗口失败：%s", error)
            return False
        return True

    def open(self, path: str = "/") -> None:
        if self.window is not None:
            self.window.load_url(self.url + path)
            self.show()

    def quit(self) -> None:
        self._quitting = True
        self._remember_geometry()
        if self.window is not None:
            self.window.destroy()

    def apply_theme(self, theme: str) -> None:
        if self.state.get("theme") != theme:
            self.state["theme"] = theme
            save_state(self.state)
        window = self.window
        if window is None or window.native is None:
            return
        dark = theme == "dark"
        try:
            if os.name == "nt":
                import ctypes

                hwnd = window.native.Handle.ToInt32()
                value = ctypes.c_int(1 if dark else 0)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), 4)  # 深色标题栏
            elif sys.platform == "darwin":
                import AppKit
                from PyObjCTools import AppHelper

                name = AppKit.NSAppearanceNameDarkAqua if dark else AppKit.NSAppearanceNameAqua
                AppHelper.callAfter(window.native.setAppearance_, AppKit.NSAppearance.appearanceNamed_(name))
        except Exception as error:  # noqa: BLE001 — 标题栏颜色不影响使用
            logger.debug("设置标题栏外观失败：%s", error)

    def pick_path(self, mode: str, current: str) -> str | None:
        import webview

        dialog = webview.FileDialog.FOLDER if mode == "folder" else webview.FileDialog.OPEN
        start = ""
        if current:
            path = Path(current).expanduser()
            folder = path if mode == "folder" else path.parent
            start = str(folder) if folder.is_dir() else ""
        try:
            result = self.window.create_file_dialog(dialog, directory=start)
        except Exception as error:  # noqa: BLE001
            logger.warning("打开文件选择框失败：%s", error)
            return None
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    # ---- 运行（主线程，阻塞到退出） ----
    def run(self, show: bool = True) -> None:
        import webview

        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.settings["ALLOW_DOWNLOADS"] = False
        geometry = initial_geometry(self.state)
        theme = self.state.get("theme") if self.state.get("theme") in BACKGROUND else "dark"
        self.window = webview.create_window(
            self.title, self.url + "/", js_api=Api(self), min_size=MIN_SIZE, hidden=not show,
            background_color=BACKGROUND[theme], text_select=True, maximized=self._maximized and show,
            localization=LOCALIZATION, **geometry)
        events = self.window.events
        events.closing += self._on_closing
        events.shown += self._on_shown
        events.loaded += self._on_loaded
        events.maximized += self._on_maximized
        events.minimized += lambda: setattr(self, "_minimized", True)
        events.restored += self._on_restored

        options = {}
        if sys.platform == "darwin":
            options["menu"] = self._mac_menu()
            self._prepare_mac()
        if os.name == "nt":
            options["gui"] = "edgechromium"
            options["icon"] = str(paths.APP_DIR.parent / "moviedb.ico") \
                if (paths.APP_DIR.parent / "moviedb.ico").is_file() else None
            if not options["icon"]:
                del options["icon"]
        storage = paths.DATA_DIR / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        if self.self_test:
            threading.Thread(target=self._self_test, daemon=True).start()
        webview.start(private_mode=False, storage_path=str(storage), localization=LOCALIZATION, **options)
        self.window = None

    # ---- 事件 ----
    def _on_closing(self):
        if self._quitting:
            return True
        self._remember_geometry()
        if not self.hide_on_close:
            self._quitting = True
            return True
        threading.Thread(target=self._hide_to_background, daemon=True).start()
        return False  # 取消关闭：只隐藏窗口

    def _hide_to_background(self):
        try:
            self.window.hide()
        except Exception as error:  # noqa: BLE001
            logger.warning("隐藏窗口失败：%s", error)
        notify = self.actions.get("notify_background")
        if os.name == "nt" and callable(notify) and not self.state.get("background_hint"):
            self.state["background_hint"] = True
            save_state(self.state)
            notify("MovieDB 仍在后台运行，可从托盘图标重新打开或退出。")

    def _on_shown(self):
        try:
            import webview

            screens = [(screen.x, screen.y, screen.width, screen.height) for screen in webview.screens]
            window = self.window
            fixed = fit_to_screens((window.x, window.y, window.width, window.height), screens)
            if fixed and not self._maximized:
                window.resize(fixed[2], fixed[3])
                window.move(fixed[0], fixed[1])
        except Exception as error:  # noqa: BLE001 — 位置修正失败不影响使用
            logger.debug("检查窗口位置失败：%s", error)

    def _on_loaded(self):
        if sys.platform == "darwin":
            try:
                from PyObjCTools import AppHelper

                webkit = self.window.native.contentView().subviews()
                for view in webkit:
                    if hasattr(view, "setAllowsBackForwardNavigationGestures_"):
                        AppHelper.callAfter(view.setAllowsBackForwardNavigationGestures_, True)  # 双指轻扫返回
            except Exception as error:  # noqa: BLE001
                logger.debug("开启轻扫返回失败：%s", error)

    def _on_maximized(self):
        self._maximized, self._minimized = True, False

    def _on_restored(self):
        if self._minimized:
            self._minimized = False  # 从最小化还原，保持原来的最大化状态
        else:
            self._maximized = False

    def _remember_geometry(self):
        window = self.window
        if window is None:
            return
        with self._lock:
            try:
                self.state["maximized"] = self._maximized
                if not self._maximized and window.width >= MIN_SIZE[0] and window.height >= MIN_SIZE[1]:
                    self.state.update(x=window.x, y=window.y, width=window.width, height=window.height)
            except Exception:  # noqa: BLE001 — 窗口已销毁
                return
            save_state(self.state)

    # ---- macOS ----
    def _mac_menu(self):
        from webview.menu import Menu, MenuAction, MenuSeparator

        def run_js(script):
            return lambda: threading.Thread(target=self.window.evaluate_js, args=(script,), daemon=True).start()

        def background(function):
            return lambda: threading.Thread(target=function, daemon=True).start()

        app_items = [MenuAction("设置…", background(lambda: self.open("/settings")))]
        folder_items = [MenuAction(label, background(action)) for label, action in
                        (("打开数据文件夹", self.actions.get("open_data")), ("查看日志", self.actions.get("open_logs")))
                        if callable(action)]
        if callable(self.actions.get("restart")):
            folder_items.append(MenuAction("重启 MovieDB", background(self.actions["restart"])))
        if folder_items:
            app_items += [MenuSeparator(), *folder_items]
        return [
            Menu("__app__", app_items),
            Menu("前往", [
                MenuAction("返回", run_js("history.back()")),
                MenuAction("前进", run_js("history.forward()")),
                MenuSeparator(),
                MenuAction("首页", background(lambda: self.open("/"))),
                MenuAction("媒体资源管理", background(lambda: self.open("/manager"))),
                MenuSeparator(),
                MenuAction("重新载入", run_js("location.reload()")),
            ]),
        ]

    def _prepare_mac(self):
        """程序坞图标的“重新打开”、⌘Q 有序退出，以及菜单快捷键。"""
        import AppKit
        import Foundation
        import objc
        from webview.platforms import cocoa

        shell = self

        class MovieDBAppDelegate(cocoa.BrowserView.AppDelegate):
            def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
                threading.Thread(target=shell.show, daemon=True).start()
                return Foundation.YES

            def applicationShouldTerminate_(self, app):
                if shell._quitting:
                    return objc.super(MovieDBAppDelegate, self).applicationShouldTerminate_(app)
                # 自己关闭窗口结束主循环，让启动器停止服务、保存状态后再退出
                threading.Thread(target=shell.quit, daemon=True).start()
                return AppKit.NSTerminateCancel

        cocoa.BrowserView._shared_app_delegate = MovieDBAppDelegate.alloc().init()

        original = cocoa.BrowserView._recreate_menus

        def recreate_menus(view, menu):
            main_menu = original(view, menu)
            _assign_shortcuts(main_menu)
            return main_menu

        cocoa.BrowserView._recreate_menus = recreate_menus

    # ---- CI 自检 ----
    def _self_test(self):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            window = self.window
            if window is not None and window.events.loaded.is_set():
                break
            time.sleep(0.2)
        else:
            print("窗口自检失败：页面没有在 90 秒内加载", flush=True)
            self.quit()
            return
        bridge = None
        for _ in range(50):
            try:
                bridge = self.window.evaluate_js(
                    "typeof window.pywebview === 'object' && window.pywebview.api"
                    " && typeof window.pywebview.api.set_theme === 'function'")
            except Exception:  # noqa: BLE001
                bridge = None
            if bridge:
                break
            time.sleep(0.2)
        title = self.window.evaluate_js("document.title")
        details = [f"标题 {title!r}", f"页面接口 {'可用' if bridge else '不可用'}"]
        import webview

        details.append(f"引擎 {getattr(webview, 'renderer', None) or '未知'}")
        bundle_ok = True
        if sys.platform == "darwin":
            import Foundation

            bundle = Foundation.NSBundle.mainBundle().bundleIdentifier()
            details.append(f"bundle {bundle}")
            bundle_ok = paths.PACKAGED is False or bool(bundle)
        self.self_test_ok = bool(bridge) and bundle_ok
        print(f"窗口自检{'通过' if self.self_test_ok else '失败'}：" + "，".join(details), flush=True)
        self.quit()


def _assign_shortcuts(menu) -> None:
    import AppKit

    for item in menu.itemArray():
        key = MAC_SHORTCUTS.get(str(item.title()))
        if key:
            item.setKeyEquivalent_(key)
            item.setKeyEquivalentModifierMask_(AppKit.NSEventModifierFlagCommand)
        if item.hasSubmenu():
            _assign_shortcuts(item.submenu())
