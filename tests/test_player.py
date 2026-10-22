"""播放器：自动选择、指定程序路径、各播放器的启动参数与 mpv / IINA 的进度读取。"""

import json
import os
import socket
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from connectors import player
from services import setup_wizard


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


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="需要 Unix 套接字")
def test_mpv_ipc_reports_progress(tmp_path, monkeypatch):
    path = str(tmp_path / "mpv.sock")
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

