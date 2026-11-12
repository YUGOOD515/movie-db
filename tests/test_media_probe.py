import json
from types import SimpleNamespace

import pytest

from connectors import media_probe


@pytest.mark.parametrize("url", ["https://offline.invalid/video.mkv", "http://127.0.0.1:5000/stream/video.mkv"])
def test_http_probe_marks_requests_to_avoid_playback_read_ahead(monkeypatch, url):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [], "format": {}}))

    monkeypatch.setattr(media_probe.subprocess, "run", run)
    media_probe.probe_video(url)
    assert calls[0][-3:] == ["-user_agent", "MovieDB-Probe", url]


def test_local_probe_does_not_pass_http_options(monkeypatch):
    def run(command, **kwargs):
        assert "-user_agent" not in command
        return SimpleNamespace(returncode=0, stdout="{}")

    monkeypatch.setattr(media_probe.subprocess, "run", run)
    media_probe.probe_video("C:/Movies/a.mkv")


def test_invalid_configured_path_falls_back_to_system_ffprobe(monkeypatch, tmp_path):
    # 浏览器曾把用户名自动填进 FFPROBE_PATH；设置页显示“已就绪”，导入也必须能用同一个 ffprobe。
    system = tmp_path / "ffprobe.exe"
    system.write_bytes(b"")
    monkeypatch.setenv("FFPROBE_PATH", "admin")
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: str(system) if name in ("ffprobe", str(system)) else None)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="{}")

    monkeypatch.setattr(media_probe.subprocess, "run", run)
    media_probe.probe_video("C:/Movies/a.mkv")
    assert calls[0][0] == str(system)


def test_missing_ffprobe_still_reports_configured_path(monkeypatch):
    monkeypatch.setenv("FFPROBE_PATH", "admin")
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: None)
    assert media_probe.executable() == "admin"
