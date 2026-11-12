import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest

from connectors.drives.base import DriveError, Entry, Link
from models import drive_store
from services import media_source, paths


class FakeDrive:
    def __init__(self, config, save, session=None):
        self.config = config
        self.expires_in = config.get("expires_in", 60)
        self.list_calls = 0
        self.link_calls = 0
        self.entries = {
            "": [Entry("a.mkv", False, "a", size=10), Entry("dir", True, "dir")],
            "dir": [Entry("b.mkv", False, "dir/b", size=20)],
            "/": [Entry("share", True, "/share"), Entry("quark", True, "/quark")],
            "/share": [Entry("a.mkv", False, "/share/a", size=10)],
        }

    def root(self):
        return Entry("", True, self.config.get("root_id", ""))

    def list(self, directory):
        self.list_calls += 1
        return self.entries[directory.id]

    def link(self, file):
        self.link_calls += 1
        time.sleep(0.02)
        return Link(f"https://offline.invalid/{file.id}", expires_in=self.expires_in)


@pytest.fixture
def source(monkeypatch):
    media_source.forget()
    monkeypatch.setattr(media_source, "_local_port", None)
    clock = [1000.0]
    monkeypatch.setattr(media_source, "_clock", lambda: clock[0])
    rows = {}
    monkeypatch.setattr(drive_store, "get_drive", lambda mount: rows.get(mount))
    monkeypatch.setattr(drive_store, "list_drives", lambda: list(rows.values()))
    monkeypatch.setattr(drive_store, "update_config", lambda mount, config: None)
    monkeypatch.setitem(media_source.DRIVERS, "fake", FakeDrive)
    yield rows, clock
    media_source.forget()


def add(rows, mount="quark", **config):
    rows[mount] = {"mount": mount, "kind": "fake", "config": config, "enabled": True}


def test_split_rejects_empty_and_parent_segments():
    assert media_source.split("/夸克/电影/a.mkv") == ("夸克", ["电影", "a.mkv"])
    for path in ("relative/a", "/quark//a", "/quark/./a", "/quark/../a", "/quark/"):
        with pytest.raises(ValueError):
            media_source.split(path)


def test_mount_resolution_and_fallback(source):
    rows, _ = source
    add(rows)
    drive, entry = media_source.resolve("/quark/dir/b.mkv")
    assert entry.id == "dir/b"
    rows.pop("quark")
    add(rows, "*", root_id="/")
    drive, entry = media_source.resolve("/share/a.mkv")
    assert entry.id == "/share/a"
    rows.clear()
    with pytest.raises(FileNotFoundError, match="找不到挂载：quark"):
        media_source.resolve("/quark/a.mkv")


def test_root_merges_mounts_before_fallback_children(source):
    rows, _ = source
    add(rows)
    add(rows, "*", root_id="/")
    result = {entry["name"]: entry for entry in media_source.list_directory("/")}
    assert result["quark"] == {"name": "quark", "is_dir": True, "size": 0, "modified": "", "created": ""}
    assert result["share"]["is_dir"] is True


def test_directory_cache_refresh_expiry_and_forget(source):
    rows, clock = source
    add(rows)
    media_source.resolve("/quark/dir/b.mkv")
    media_source.resolve("/quark/dir/b.mkv")
    drive = media_source.drive_for("quark")
    assert drive.list_calls == 2
    media_source.resolve("/quark/dir/b.mkv", refresh=True)
    assert drive.list_calls == 4
    media_source.resolve("/quark/dir/b.mkv")
    clock[0] += 1801
    media_source.resolve("/quark/dir/b.mkv")
    assert drive.list_calls == 6
    media_source.forget("quark")
    media_source.resolve("/quark/dir/b.mkv")
    assert media_source.drive_for("quark").list_calls == 2


def test_link_cache_expiry_fresh_and_concurrent_fetch(source):
    rows, clock = source
    add(rows)
    media_source.open_link("/quark/a.mkv")
    media_source.open_link("/quark/a.mkv")
    drive = media_source.drive_for("quark")
    assert drive.link_calls == 1
    clock[0] += 61
    media_source.open_link("/quark/a.mkv")
    assert drive.link_calls == 2
    media_source.open_link("/quark/a.mkv", fresh=True)
    assert drive.link_calls == 3

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: media_source.open_link("/quark/dir/b.mkv"), range(4)))
    assert drive.link_calls == 4


def test_link_without_expiry_is_capped_at_thirty_minutes(source):
    rows, clock = source
    add(rows, expires_in=None)
    media_source.open_link("/quark/a.mkv")
    clock[0] += 1799
    media_source.open_link("/quark/a.mkv")
    assert media_source.drive_for("quark").link_calls == 1
    clock[0] += 2
    media_source.open_link("/quark/a.mkv")
    assert media_source.drive_for("quark").link_calls == 2


def test_stream_url_signature_and_key_reuse(source, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setenv("MOVIEDB_PORT", "5050")
    url = media_source.stream_url("/夸克/电影/a.mkv")
    parts = urlsplit(url)
    assert parts.netloc == "127.0.0.1:5050" and parts.path.endswith("/a.mkv")
    token = parts.path.split("/stream/", 1)[1].split("/", 1)[0]
    assert media_source.parse_stream_token(token) == "/夸克/电影/a.mkv"
    key = (tmp_path / "stream.key").read_bytes()
    media_source.stream_url("/夸克/电影/a.mkv")
    assert (tmp_path / "stream.key").read_bytes() == key
    with pytest.raises(ValueError):
        media_source.parse_stream_token(token[:-1] + ("0" if token[-1] != "0" else "1"))


def test_get_file_and_parse_source(source, monkeypatch, tmp_path):
    rows, _ = source
    add(rows)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    result = media_source.get_file("/quark/a.mkv")
    assert result["size"] == 10 and result["signed_url"] == result["raw_url"]
    assert media_source.parse_source("http://host/d/%E7%94%B5%E5%BD%B1/a.mkv?sign=x") == "/电影/a.mkv"
    assert media_source.parse_source("/quark/a.mkv") == "/quark/a.mkv"


def test_disabled_mount_does_not_fall_back_to_passthrough(source):
    """停用同名挂载后路径不可用，而不是悄悄改由透传提供；只有显式移交才交给透传。"""
    rows, _ = source
    add(rows, "quark")
    add(rows, "*", root_id="/")
    assert media_source.resolve("/quark/dir/b.mkv")[1].id == "dir/b"
    rows["quark"]["enabled"] = False
    media_source.forget()
    with pytest.raises(media_source.SourceUnavailable, match="已停用"):
        media_source.resolve("/quark/a.mkv")
    assert "quark" not in {entry["name"] for entry in media_source.list_directory("/")}
    rows["quark"]["delegated"] = True
    assert media_source.route("quark") == ("*", "")
    assert "quark" in {entry["name"] for entry in media_source.list_directory("/")}
    rows["*"]["enabled"] = False
    with pytest.raises(media_source.SourceUnavailable, match="未启用"):
        media_source.resolve("/quark/a.mkv")


def test_stream_url_follows_the_listening_port(source, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setenv("MOVIEDB_PORT", "5000")
    media_source.remember_local_port({"SERVER_NAME": "8.8.8.8", "SERVER_PORT": "443"})
    assert urlsplit(media_source.stream_url("/q/a.mkv")).port == 5000
    media_source.remember_local_port({"SERVER_NAME": "127.0.0.1", "SERVER_PORT": "5055"})
    assert urlsplit(media_source.stream_url("/q/a.mkv")).port == 5055
