"""桌面版：界面打开方式、单实例唤起接口、窗口位置与状态的纯函数。"""

import argparse
import os
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
import desktop
import desktop_window

DESKTOP = {"X-MovieDB-Desktop": "1"}


def _args(**values):
    defaults = {"self_test": False, "no_browser": False, "browser": False}
    return argparse.Namespace(**{**defaults, **values})


def test_ui_mode_defaults_to_window_and_respects_options():
    assert desktop.ui_mode(_args(), {}) == desktop.UI_WINDOW
    assert desktop.ui_mode(_args(), {"MOVIEDB_UI": " Browser "}) == desktop.UI_BROWSER
    assert desktop.ui_mode(_args(browser=True), {}) == desktop.UI_BROWSER
    assert desktop.ui_mode(_args(no_browser=True), {"MOVIEDB_UI": "browser"}) == desktop.UI_NONE
    assert desktop.ui_mode(_args(self_test=True, no_browser=True), {}) == desktop.UI_WINDOW


@pytest.fixture
def client():
    previous = {key: movie_app.app.config.get(key) for key in ("READ_ONLY", "SHOW_WINDOW_HANDLER")}
    movie_app.app.config.update(READ_ONLY=False)
    movie_app.app.config.pop("SHOW_WINDOW_HANDLER", None)
    yield movie_app.app.test_client()
    movie_app.app.config.update(previous)


def test_show_window_endpoint_requires_desktop_header(client):
    calls = []
    movie_app.app.config["SHOW_WINDOW_HANDLER"] = lambda: calls.append(1) or True
    assert client.post("/desktop/show").status_code == 404
    assert client.post("/desktop/show", headers={**DESKTOP, "Cf-Connecting-Ip": "1.2.3.4"}).status_code == 404
    assert client.get("/desktop/show", headers=DESKTOP).status_code == 405
    assert not calls
    response = client.post("/desktop/show", headers=DESKTOP)
    assert response.get_json() == {"shown": True} and calls == [1]


def test_show_window_endpoint_without_window(client):
    assert client.post("/desktop/show", headers=DESKTOP).get_json() == {"shown": False}
    movie_app.app.config["READ_ONLY"] = True
    assert client.post("/desktop/show", headers=DESKTOP).status_code in (404, 503)


def test_initial_geometry_uses_saved_size_or_default():
    default = {"width": 1440, "height": 900, "x": None, "y": None}
    assert desktop_window.initial_geometry({}) == default
    assert desktop_window.initial_geometry({"width": 500, "height": 400, "x": 1, "y": 2}) == default
    assert desktop_window.initial_geometry({"width": True, "height": 900}) == default
    saved = {"width": 1200.0, "height": 800, "x": -20, "y": 40}
    assert desktop_window.initial_geometry(saved, "win32") == {"width": 1200, "height": 800, "x": -20, "y": 40}
    # macOS 的位置交给 AppKit 的窗口自动保存
    assert desktop_window.initial_geometry(saved, "darwin") == default


def test_same_origin_path_keeps_only_in_app_pages():
    origin = "http://127.0.0.1:5000"
    assert desktop_window.same_origin_path(origin + "/manager?q=a", origin) == "/manager?q=a"
    assert desktop_window.same_origin_path(origin + "/settings#port", origin) == "/settings"
    assert desktop_window.same_origin_path("https://www.themoviedb.org/movie/1", origin) is None
    assert desktop_window.same_origin_path("about:blank", origin) is None
    assert desktop_window.same_origin_path(None, origin) is None


def test_choose_port_falls_back_only_for_default_port():
    states = {5000: False, 5050: False, 5051: None}  # False：被其他程序占用；None：没有程序在监听
    probe = lambda url: states.get(int(url.rsplit(":", 1)[1]))  # noqa: E731
    free = lambda port: states.get(port, None) is None  # noqa: E731
    assert desktop.choose_port(5000, False, probe, free) == (5051, "free")
    assert desktop.choose_port(5000, True, probe, free) == (5000, "busy")
    states[5050] = True  # 之前换到 5050 的 MovieDB 仍在运行
    assert desktop.choose_port(5000, False, probe, free) == (5050, "running")
    assert desktop.choose_port(5000, False, lambda url: None, lambda port: True) == (5000, "free")
    assert desktop.choose_port(5000, False, lambda url: False, lambda port: False) == (5000, "busy")


def test_port_busy_message_mentions_airplay_on_mac(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    assert "隔空播放接收器" in desktop._port_busy_message(5000)
    assert "隔空播放接收器" not in desktop._port_busy_message(5123)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    assert "隔空播放接收器" not in desktop._port_busy_message(5000)


def test_fit_to_screens_moves_lost_or_oversized_windows():
    main, side = (0, 0, 1920, 1080), (1920, 0, 2560, 1440)
    assert desktop_window.fit_to_screens((100, 100, 1440, 900), [main, side]) is None
    assert desktop_window.fit_to_screens((2000, 100, 1440, 900), [main, side]) is None
    # 外接显示器拔掉后，窗口回到主屏幕中间
    assert desktop_window.fit_to_screens((2000, 100, 1440, 900), [main]) == (240, 90, 1440, 900)
    # 比屏幕还大时缩小到屏幕的九成
    assert desktop_window.fit_to_screens((0, 0, 2400, 1300), [main]) == (96, 54, 1728, 972)
    assert desktop_window.fit_to_screens((0, 0, 100, 100), []) is None


def test_is_external_compares_origin():
    origin = "http://127.0.0.1:5000"
    assert desktop_window.is_external("https://www.themoviedb.org/movie/1", origin)
    assert not desktop_window.is_external("http://127.0.0.1:5000/settings", origin)
    assert not desktop_window.is_external("mailto:someone@example.com", origin)


def test_window_state_round_trip(tmp_path):
    path = tmp_path / "window.json"
    assert desktop_window.load_state(path) == {}
    desktop_window.save_state({"width": 1200, "theme": "light"}, path)
    assert desktop_window.load_state(path) == {"width": 1200, "theme": "light"}
    path.write_text("[1, 2]", encoding="utf-8")
    assert desktop_window.load_state(path) == {}
