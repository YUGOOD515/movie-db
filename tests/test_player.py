"""播放器：自动选择、指定程序路径、各播放器的启动参数与 mpv / IINA 的进度读取。"""

import json
import os
import socket
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from connectors import player
from connectors.drives.base import Entry, Link
from services import media_source, playback, setup_wizard


@pytest.fixture
def no_players(monkeypatch):
    for name in ("PLAYER", "PLAYER_PATH", "MPC_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(player, "_candidates", lambda kind, env=os.environ: [])


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
    tracked = kind in player.TRACKS_PROGRESS and not player.IS_WINDOWS
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
