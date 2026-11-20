"""播放器：自动选择、指定程序路径、各播放器的启动参数与 mpv / IINA 的进度读取。"""

import json
import multiprocessing.connection
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from connectors import player
from connectors.drives.base import Entry, Link
from services import media_source, paths, playback, setup_wizard


@pytest.fixture
def no_players(monkeypatch):
    for name in ("PLAYER", "PLAYER_PATH", "MPC_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [])
    monkeypatch.setattr(player, "bundled_mpv", lambda: None)


def _executable(folder: Path, name: str) -> str:
    path = folder / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_auto_falls_back_to_system_default(no_players):
    assert player.resolve_player() == ("system", None)


def test_auto_prefers_platform_order(no_players, monkeypatch, tmp_path):
    found = {"mpv": _executable(tmp_path, "mpv"), "iina": _executable(tmp_path, "iina-cli")}
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [found.get(kind, "")])
    monkeypatch.setattr(player, "_auto_order", lambda: ("iina", "mpv", "vlc"))
    assert player.resolve_player() == ("iina", found["iina"])


def test_explicit_player_and_custom_path(no_players, monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYER", "mpv")
    with pytest.raises(player.PlayerNotFound, match="mpv"):
        player.resolve_player()
    path = _executable(tmp_path, "my-mpv")
    assert player.resolve_player({"PLAYER_PATH": path}) == ("mpv", path)
    monkeypatch.setenv("PLAYER", "nonsense")
    with pytest.raises(player.PlayerNotFound):
        player.resolve_player()


def test_custom_path_guesses_kind_and_accepts_app_bundle(no_players, monkeypatch, tmp_path):
    vlc = _executable(tmp_path, "vlc")
    assert player.resolve_player({"PLAYER_PATH": vlc}) == ("vlc", vlc)
    other = _executable(tmp_path, "someplayer")
    assert player.resolve_player({"PLAYER_PATH": other}) == ("custom", other)
    bundle = tmp_path / "IINA.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    cli = _executable(bundle / "Contents" / "MacOS", "iina-cli")
    assert player.resolve_player({"PLAYER_PATH": str(bundle)}) == ("iina", cli)
    with pytest.raises(player.PlayerNotFound):
        player.resolve_player({"PLAYER_PATH": str(tmp_path / "missing")})


@pytest.mark.parametrize(("kind", "expected"), [
    ("mpv", ["--force-window=immediate", "--start=90.500"]),
    ("iina", ["--mpv-start=90.500"]),
    ("vlc", ["--start-time=90"]),
])
def test_launch_arguments(monkeypatch, kind, expected):
    monkeypatch.setattr(player, "resolve_player", lambda: (kind, "/bin/" + kind))
    with patch.object(player, "_popen") as popen, patch.object(player.threading, "Thread") as thread:
        player.play_video("https://media.test/a.mkv", start_position_ms=90_500, progress_callback=lambda *_: None)
    command = popen.call_args.args[0]
    assert command[0] == "/bin/" + kind and command[-1] == "https://media.test/a.mkv"
    assert command[1:1 + len(expected)] == expected
    tracked = kind == "mpv" or (kind == "iina" and not player.IS_WINDOWS)
    assert any("ipc-server=" in part for part in command) == tracked
    assert thread.called == tracked


def test_system_default_opens_link(monkeypatch):
    monkeypatch.setattr(player, "resolve_player", lambda: ("system", None))
    with patch.object(player, "_open_with_system") as opener:
        player.play_video("https://media.test/a.mkv", progress_callback=lambda *_: None)
    opener.assert_called_once_with("https://media.test/a.mkv")


@pytest.fixture
def unix_socket_path():
    # macOS limits AF_UNIX paths to roughly 104 bytes; pytest's default
    # /private/var/folders/... hierarchy already exceeds that on hosted runners.
    with tempfile.TemporaryDirectory(prefix="mpv-", dir="/tmp") as folder:
        yield str(Path(folder) / "mpv.sock")


@pytest.mark.skipif(player.IS_WINDOWS or not hasattr(socket, "AF_UNIX"), reason="mpv/IINA 的 Unix IPC 仅用于 macOS/Linux")
def test_mpv_ipc_reports_progress(unix_socket_path, monkeypatch):
    path = unix_socket_path
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    values = {"time-pos": 12.5, "duration": 100.0}

    def serve():
        connection, _ = server.accept()
        with connection, connection.makefile("rw", encoding="utf-8") as stream:
            for count, line in enumerate(stream):
                request = json.loads(line)
                if count >= 4:  # 播放器退出
                    return
                stream.write(json.dumps({"event": "property-change"}) + "\n")  # 事件通知要被跳过
                name = request["command"][1]
                stream.write(json.dumps({"request_id": request["request_id"], "error": "success",
                                         "data": values[name]}) + "\n")
                stream.flush()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(player, "IPC_POLL_INTERVAL", 0)
    saved = []
    generation = player._next_playback_generation()
    player._monitor_mpv_playback(generation, path, lambda position, duration: saved.append((position, duration)))
    thread.join(2)
    server.close()
    assert saved and saved[-1] == (12_500, 100_000)


def test_setup_check_reports_progress_support(no_players, tmp_path):
    result = setup_wizard.test_player({})
    assert result["ok"] and result["warnings"]  # 系统默认程序不记录进度
    mpv = _executable(tmp_path, "mpv")
    result = setup_wizard.test_player({"PLAYER": "mpv", "PLAYER_PATH": mpv})
    assert result["ok"] and "可以记录观看进度" in result["details"] and not result["warnings"]
    result = setup_wizard.test_player({"PLAYER": "iina"})
    assert not result["ok"]


def test_setup_and_settings_name_the_bundled_mpv(no_players, monkeypatch, tmp_path):
    from services import paths, settings

    bundled = _executable(tmp_path, "mpv.exe")
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    monkeypatch.setattr(player, "bundled_mpv", lambda: bundled)
    result = setup_wizard.test_player({})
    assert result["ok"] and result["details"] == ["随安装包提供的 mpv", "可以记录观看进度"]
    assert settings._player_status()["text"].endswith(" · 内置 mpv")
    other = _executable(tmp_path, "my-mpv")
    result = setup_wizard.test_player({"PLAYER_PATH": other})
    assert "随安装包提供的 mpv" not in result["details"]


def test_play_route_reports_missing_player(monkeypatch):
    with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
        import app as movie_app
    monkeypatch.setattr(movie_app, "get_playable_file", lambda db, file_id: {"alist_path": "/a.mkv"})
    monkeypatch.setattr(movie_app, "get_db", lambda: None)
    monkeypatch.setenv("PLAYER", "iina")
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [])
    monkeypatch.delenv("PLAYER_PATH", raising=False)
    client = movie_app.app.test_client()
    with patch.object(player, "play_video") as play_video:
        response = client.post("/play/1", headers={"Referer": "http://localhost/title/1"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/title/1")
    play_video.assert_not_called()
    with client.session_transaction() as session:
        assert any("IINA" in message for _, message in session.get("_flashes", []))


def test_match_subtitles_handles_single_movie_dir_tv_episodes_and_mount_root():
    exts = media_source.VIDEO_EXTENSIONS
    # 独立电影目录：中文命名的外挂字幕和 Subs 子目录都能配对，且简中 ASS 排第一
    matched = playback.match_subtitles(
        "/baidu/阿凡达 (2009)/Avatar.2009.BluRay.mkv",
        [
            {"name": "Avatar.2009.BluRay.mkv", "is_dir": False},
            {"name": "阿凡达.繁体.srt", "is_dir": False},
            {"name": "阿凡达.简英双语.ass", "is_dir": False},
            {"name": "Subs", "is_dir": True},
        ],
        [("/baidu/阿凡达 (2009)/Subs", [{"name": "Avatar.2009.BluRay.eng.srt", "is_dir": False}])],
        video_extensions=exts,
    )
    assert matched == [
        "/baidu/阿凡达 (2009)/阿凡达.简英双语.ass",
        "/baidu/阿凡达 (2009)/阿凡达.繁体.srt",
        "/baidu/阿凡达 (2009)/Subs/Avatar.2009.BluRay.eng.srt",
    ]
    # 同语言时同前缀优先，简中整体优先于其他语言
    ranked = playback.match_subtitles(
        "/baidu/阿凡达 (2009)/Avatar.mkv",
        [
            {"name": "Avatar.mkv", "is_dir": False},
            {"name": "Avatar.eng.srt", "is_dir": False},
            {"name": "其他名.简中.srt", "is_dir": False},
            {"name": "Avatar.chs.ass", "is_dir": False},
        ],
        video_extensions=exts,
    )
    assert ranked == [
        "/baidu/阿凡达 (2009)/Avatar.chs.ass",
        "/baidu/阿凡达 (2009)/其他名.简中.srt",
        "/baidu/阿凡达 (2009)/Avatar.eng.srt",
    ]
    # 多集目录：只匹配对应集号，不串集
    tv = playback.match_subtitles(
        "/baidu/Severance/[GroupA] Severance.S01E02.1080p.mkv",
        [
            {"name": "[GroupA] Severance.S01E01.1080p.mkv", "is_dir": False},
            {"name": "[GroupA] Severance.S01E02.1080p.mkv", "is_dir": False},
            {"name": "[SubGroup] Severance S01E01.chs.ass", "is_dir": False},
            {"name": "[SubGroup] Severance S01E02.chs.ass", "is_dir": False},
        ],
        video_extensions=exts,
    )
    assert tv == ["/baidu/Severance/[SubGroup] Severance S01E02.chs.ass"]
    # 网盘挂载根目录即使只有一个视频，也不乱抓无关文件名的字幕
    root_subs = playback.match_subtitles(
        "/baidu/Avatar.mkv",
        [
            {"name": "Avatar.mkv", "is_dir": False},
            {"name": "Titanic.chs.srt", "is_dir": False},
            {"name": "Avatar.zh-CN.srt", "is_dir": False},
        ],
        video_extensions=exts,
    )
    assert root_subs == ["/baidu/Avatar.zh-CN.srt"]


def test_launch_arguments_include_external_subtitles(monkeypatch):
    subs = ["/tmp/subs/a.chs.ass", "/tmp/subs/a.eng.srt"]
    cases = {
        "mpc-hc": ["/bin/mpc-hc", "https://media.test/a.mkv", "/sub", subs[0], "/sub", subs[1]],
        "mpv": [f"--sub-file={subs[0]}", f"--sub-file={subs[1]}"],
        "iina": [f"--mpv-sub-files={os.pathsep.join(subs)}"],
        "vlc": [f"--sub-file={subs[0]}"],
    }
    for kind, expected in cases.items():
        monkeypatch.setattr(player, "resolve_player", lambda k=kind: (k, "/bin/" + k))
        with patch.object(player, "_popen") as popen, patch.object(player.threading, "Thread"):
            player.play_video("https://media.test/a.mkv", subtitles=subs)
        command = popen.call_args.args[0]
        if kind == "mpc-hc":
            assert command == expected
        else:
            for item in expected:
                assert item in command
            assert command[-1] == "https://media.test/a.mkv"


def test_prepare_subtitles_stages_remote_file_and_play_route_passes_it(monkeypatch, tmp_path):
    monkeypatch.setattr(media_source.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(media_source, "list_directory", lambda path, refresh=False: [
        {"name": "Avatar.mkv", "is_dir": False, "size": 1000},
        {"name": "Avatar.chs.srt", "is_dir": False, "size": 12},
    ])
    monkeypatch.setattr(
        media_source,
        "open_link",
        lambda path, fresh=False: (Link(url="https://cdn.test/sub.srt", headers={"User-Agent": "test"}), Entry(id="1", name="Avatar.chs.srt", is_dir=False, size=12)),
    )

    class DummyResponse:
        status_code = 200
        def iter_content(self, _chunk):
            yield b"1\n00:00:01\n\n"
        def close(self):
            pass

    class DummySession:
        def __init__(self):
            self.calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return DummyResponse()

    session = DummySession()
    monkeypatch.setattr(media_source, "resolve", lambda path, refresh=False: (type("D", (), {"session": session})(), None))
    first = media_source.prepare_subtitles("/baidu/Movies/Avatar.mkv")
    assert len(first) == 1 and Path(first[0]).read_bytes() == b"1\n00:00:01\n\n"
    assert session.calls == 1
    # 大小未变时直接命中本地缓存，不重复下载
    second = media_source.prepare_subtitles("/baidu/Movies/Avatar.mkv")
    assert second == first and session.calls == 1

    with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
        import app as movie_app
    monkeypatch.setattr(movie_app, "get_playable_file", lambda db, file_id: {"alist_path": "/baidu/Movies/Avatar.mkv"})
    monkeypatch.setattr(movie_app, "get_db", lambda: None)
    monkeypatch.setattr(player, "resolve_player", lambda: ("mpc-hc", r"C:\MPC-HC\mpc-hc64.exe"))
    client = movie_app.app.test_client()
    with patch.object(player, "play_video") as play_video:
        response = client.post("/play/1", headers={"Origin": "http://localhost"})
    assert response.status_code == 204
    assert play_video.call_args.kwargs["subtitles"] == first



def test_prepare_subtitles_does_not_wait_for_slow_download(monkeypatch, tmp_path):
    """字幕下载超过等待时间时先不带它启动播放器，已下好的照常传入。"""
    import threading

    monkeypatch.setattr(media_source.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(media_source, "list_directory", lambda path, refresh=False: [
        {"name": "Avatar.mkv", "is_dir": False, "size": 1000},
        {"name": "Avatar.chs.srt", "is_dir": False, "size": 12},
        {"name": "Avatar.eng.srt", "is_dir": False, "size": 12},
    ])
    release = threading.Event()

    def stage(sub_path, cache_dir):
        if sub_path.endswith("eng.srt"):
            release.wait(5)
            return None
        return str(tmp_path / "Avatar.chs.srt")

    monkeypatch.setattr(media_source, "_stage_subtitle_file", stage)
    try:
        assert media_source.prepare_subtitles("/baidu/Movies/Avatar.mkv", wait=0.3) == [str(tmp_path / "Avatar.chs.srt")]
    finally:
        release.set()


def test_auto_prefers_bundled_mpv_over_mpc_hc(monkeypatch, tmp_path):
    for name in ("PLAYER", "PLAYER_PATH", "MPC_PATH"):
        monkeypatch.delenv(name, raising=False)
    bundled = _executable(tmp_path, "mpv.exe")
    mpc = _executable(tmp_path, "mpc-hc64.exe")
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    monkeypatch.setattr(player, "IS_MACOS", False)
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [mpc] if kind == "mpc-hc" else [])
    monkeypatch.setattr(player, "_auto_order", lambda: ("mpc-hc", "mpv", "vlc"))
    assert player.resolve_player() == ("mpv", bundled)
    assert player.resolve_player({"PLAYER": "auto"}) == ("mpv", bundled)


def test_auto_uses_platform_order_when_no_bundled_mpv(monkeypatch, tmp_path):
    for name in ("PLAYER", "PLAYER_PATH", "MPC_PATH"):
        monkeypatch.delenv(name, raising=False)
    found = {"mpc-hc": _executable(tmp_path, "mpc-hc64.exe"), "mpv": _executable(tmp_path, "mpv.exe")}
    monkeypatch.setattr(paths, "BUNDLED_MPV", tmp_path / "missing-mpv.exe")
    monkeypatch.setattr(player, "IS_MACOS", False)
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [found.get(kind, "")])
    monkeypatch.setattr(player, "_auto_order", lambda: ("mpc-hc", "mpv", "vlc"))
    assert player.resolve_player() == ("mpc-hc", found["mpc-hc"])


def test_player_path_overrides_bundled_mpv(monkeypatch, tmp_path):
    for name in ("PLAYER", "PLAYER_PATH", "MPC_PATH"):
        monkeypatch.delenv(name, raising=False)
    bundled = _executable(tmp_path, "bundled-mpv.exe")
    custom = _executable(tmp_path, "my-mpv.exe")
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    monkeypatch.setattr(player, "IS_MACOS", False)
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [])
    assert player.resolve_player({"PLAYER_PATH": custom}) == ("mpv", custom)
    assert player.resolve_player({"PLAYER": "auto", "PLAYER_PATH": custom}) == ("mpv", custom)
    assert player.resolve_player({"PLAYER": "mpv", "PLAYER_PATH": custom}) == ("mpv", custom)
    assert player.resolve_player({"PLAYER_PATH": "   "}) == ("mpv", bundled)
    assert player.resolve_player({"PLAYER": "mpv", "PLAYER_PATH": "   "}) == ("mpv", bundled)


def test_explicit_mpv_prefers_bundled_then_falls_back_to_candidates(monkeypatch, tmp_path):
    for name in ("PLAYER", "PLAYER_PATH", "MPC_PATH"):
        monkeypatch.delenv(name, raising=False)
    bundled = _executable(tmp_path, "bundled-mpv.exe")
    system_mpv = _executable(tmp_path, "system-mpv.exe")
    monkeypatch.setattr(player, "IS_MACOS", False)
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [system_mpv] if kind == "mpv" else [])
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    assert player.resolve_player({"PLAYER": "mpv"}) == ("mpv", bundled)
    monkeypatch.setattr(paths, "BUNDLED_MPV", tmp_path / "missing-mpv.exe")
    assert player.resolve_player({"PLAYER": "mpv"}) == ("mpv", system_mpv)


def test_explicit_mpc_hc_ignores_bundled_mpv(monkeypatch, tmp_path):
    for name in ("PLAYER", "PLAYER_PATH", "MPC_PATH"):
        monkeypatch.delenv(name, raising=False)
    bundled = _executable(tmp_path, "bundled-mpv.exe")
    mpc = _executable(tmp_path, "mpc-hc64.exe")
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    monkeypatch.setattr(player, "IS_MACOS", False)
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [mpc] if kind == "mpc-hc" else [])
    assert player.resolve_player({"PLAYER": "mpc-hc"}) == ("mpc-hc", mpc)


@pytest.mark.parametrize(("version", "allowed"), [
    ("14.6", False),
    ("15.0", True),
    ("26.1", True),
    ("", True),
])
def test_bundled_mpv_checks_macos_version(monkeypatch, tmp_path, version, allowed):
    bundled = _executable(tmp_path, "mpv")
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    monkeypatch.setattr(player, "IS_MACOS", True)
    monkeypatch.setattr(player, "_macos_version", lambda: version)
    assert player.bundled_mpv() == (bundled if allowed else None)


def test_is_bundled_matches_same_file_and_handles_missing(monkeypatch, tmp_path):
    mpv_dir = tmp_path / "mpv"
    mpv_dir.mkdir()
    bundled = _executable(mpv_dir, "mpv.exe")
    other = _executable(tmp_path, "mpv.exe")
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    assert player.is_bundled(bundled) is True
    assert player.is_bundled(str(mpv_dir / ".." / "mpv" / "mpv.exe")) is True
    assert player.is_bundled(other) is False
    assert player.is_bundled(None) is False
    assert player.is_bundled("") is False
    assert player.is_bundled(str(tmp_path / "missing" / "mpv.exe")) is False


def test_bundled_mpv_config_dir_launch_argument(monkeypatch, tmp_path):
    bundled = _executable(tmp_path, "mpv.exe")
    user_mpv = _executable(tmp_path, "user-mpv.exe")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(paths, "BUNDLED_MPV", Path(bundled))
    monkeypatch.setattr(paths, "BUNDLED_MPV_CONFIG", config_dir)

    monkeypatch.setattr(player, "resolve_player", lambda: ("mpv", bundled))
    with patch.object(player, "_popen") as popen, patch.object(player.threading, "Thread"):
        player.play_video("https://media.test/a.mkv", start_position_ms=10_000, subtitles=["/sub/a.srt"])
    command = popen.call_args.args[0]
    assert command[0] == bundled
    assert command[1:4] == ["--force-window=immediate", "--start=10.000", f"--config-dir={config_dir}"]
    assert command[4].startswith("--input-ipc-server=")
    assert command[5:] == ["--sub-file=/sub/a.srt", "https://media.test/a.mkv"]

    monkeypatch.setattr(paths, "BUNDLED_MPV_CONFIG", tmp_path / "missing-config")
    with patch.object(player, "_popen") as popen, patch.object(player.threading, "Thread"):
        player.play_video("https://media.test/a.mkv", start_position_ms=10_000)
    command = popen.call_args.args[0]
    assert not any(arg.startswith("--config-dir=") for arg in command)
    assert command[3].startswith("--input-ipc-server=")

    monkeypatch.setattr(paths, "BUNDLED_MPV_CONFIG", config_dir)
    monkeypatch.setattr(player, "resolve_player", lambda: ("mpv", user_mpv))
    with patch.object(player, "_popen") as popen, patch.object(player.threading, "Thread"):
        player.play_video("https://media.test/a.mkv", start_position_ms=10_000)
    command = popen.call_args.args[0]
    assert not any(arg.startswith("--config-dir=") for arg in command)


def test_windows_mpv_uses_named_pipe_and_starts_monitor(monkeypatch):
    monkeypatch.setattr(player, "IS_WINDOWS", True)
    monkeypatch.setattr(player, "resolve_player", lambda: ("mpv", r"C:\mpv\mpv.exe"))
    callback = lambda *_: None  # noqa: E731
    with patch.object(player, "_popen") as popen, patch.object(player.threading, "Thread") as thread:
        player.play_video("https://media.test/a.mkv", progress_callback=callback)
    generation = player._current_playback_generation()
    expected_pipe = rf"\\.\pipe\moviedb-mpv-{os.getpid()}-{generation}"
    command = popen.call_args.args[0]
    assert f"--input-ipc-server={expected_pipe}" in command
    assert thread.called
    assert thread.call_args.kwargs["target"] is player._monitor_mpv_playback
    assert thread.call_args.kwargs["args"] == (generation, expected_pipe, callback)
    thread.return_value.start.assert_called_once()


@pytest.mark.skipif(os.name != "nt", reason="Windows 命名管道 IPC 测试仅在 Windows 运行")
def test_mpv_windows_named_pipe_reports_progress(monkeypatch):
    generation = player._next_playback_generation()
    pipe_name = rf"\\.\pipe\moviedb-test-mpv-{os.getpid()}-{generation}"
    listener = multiprocessing.connection.Listener(address=pipe_name, family="AF_PIPE")
    values = {"time-pos": 12.5, "duration": 100.0}
    closed_at = []

    def serve():
        try:
            with listener:
                conn = listener.accept()
                with conn:
                    for count in range(5):
                        raw = conn.recv_bytes()
                        for line in raw.decode("utf-8").splitlines():
                            request = json.loads(line)
                            if count >= 4:  # 模拟播放器退出，关闭管道
                                return
                            event = json.dumps({"event": "property-change"}) + "\n"
                            conn.send_bytes(event.encode("utf-8"))
                            name = request["command"][1]
                            reply = {
                                "request_id": request["request_id"],
                                "error": "success",
                                "data": values[name],
                            }
                            conn.send_bytes((json.dumps(reply) + "\n").encode("utf-8"))
        finally:
            closed_at.append(time.monotonic())

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()
    monkeypatch.setattr(player, "IPC_POLL_INTERVAL", 0.01)
    saved = []
    monitor_thread = threading.Thread(
        target=player._monitor_mpv_playback,
        args=(generation, pipe_name, lambda position, duration: saved.append((position, duration))),
        daemon=True,
    )
    monitor_thread.start()
    monitor_thread.join(5)
    finished_at = time.monotonic()
    server_thread.join(2)
    assert not monitor_thread.is_alive()
    assert saved == [(12_500, 100_000)]
    assert closed_at and finished_at - closed_at[0] < 5


def test_windows_monitor_does_not_unlink_pipe_path(monkeypatch):
    monkeypatch.setattr(player, "IS_WINDOWS", True)
    closed = []

    class FakeClient:
        def __init__(self, path):
            self.path = path

        def get(self, name):
            raise ConnectionError("播放器已关闭")

        def close(self):
            closed.append(self.path)

    unlinked = []
    monkeypatch.setattr(player, "_MpvClient", FakeClient)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: unlinked.append(self))
    generation = player._next_playback_generation()
    pipe_path = rf"\\.\pipe\moviedb-mpv-{os.getpid()}-{generation}"
    player._monitor_mpv_playback(generation, pipe_path, lambda *_: None)
    assert closed == [pipe_path]
    assert unlinked == []
