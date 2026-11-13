from unittest.mock import Mock

import pytest

from connectors.drives.base import AuthError
from models import drive_store
from services import drive_keepalive, drives, media_source


@pytest.fixture(autouse=True)
def clean_state():
    drive_keepalive.forget()
    yield
    drive_keepalive.forget()


def fake_drive(interval=600, result="已连接", error=None):
    drive = Mock(keepalive_interval=interval)
    drive.keepalive.side_effect = error if error else (lambda: result)
    return drive


def test_runs_enabled_drives_on_interval_and_records_state(monkeypatch):
    drive_store.save_drive("A", "quark", {"cookie": "x"}, True)
    drive_store.save_drive("B", "quark", {"cookie": "y"}, False)
    instance = fake_drive()
    monkeypatch.setattr(media_source, "drive_for", lambda mount: instance)

    assert drive_keepalive.run_due(now=1000) == ["A"]
    assert drive_store.get_drive("A")["status"] == "已连接"
    assert drive_keepalive.state("A")["ok"] and not drive_keepalive.state("B")
    # 间隔内不重复执行；间隔（含 ±5% 抖动）之后再次执行
    assert drive_keepalive.run_due(now=1000 + 500) == []
    assert drive_keepalive.run_due(now=1000 + 700) == ["A"]
    assert instance.keepalive.call_count == 2


def test_failure_updates_status_and_drives_without_interval_are_skipped(monkeypatch):
    drive_store.save_drive("A", "quark", {"cookie": "x"}, True)
    drive_store.save_drive("L", "local", {"root": "."}, True)
    instances = {"A": fake_drive(error=AuthError("夸克网盘 Cookie 已失效，请重新复制")), "L": fake_drive(interval=0)}
    monkeypatch.setattr(media_source, "drive_for", instances.__getitem__)

    assert drive_keepalive.run_due(now=0) == ["A"]
    assert drive_store.get_drive("A")["status"] == "夸克网盘 Cookie 已失效，请重新复制"
    assert drive_keepalive.state("A") == {"at": drive_keepalive.state("A")["at"], "ok": False}
    assert not instances["L"].keepalive.called
    row = drives.public(drive_store.get_drive("A"))
    assert row["status_state"] == "fail" and row["keepalive_text"] == ""


def test_public_shows_last_renewal_time(monkeypatch):
    drive_store.save_drive("A", "quark", {"cookie": "x"}, True)
    monkeypatch.setattr(media_source, "drive_for", lambda mount: fake_drive())
    drive_keepalive.run_due(now=0)
    assert drives.public(drive_store.get_drive("A"))["keepalive_text"].endswith("已续期")


def test_start_respects_disable_flag(monkeypatch):
    monkeypatch.setenv("MOVIEDB_KEEPALIVE_DISABLED", "1")
    monkeypatch.setattr(drive_keepalive, "_thread", None)
    drive_keepalive.start()
    assert drive_keepalive._thread is None
