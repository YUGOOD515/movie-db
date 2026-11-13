import json
import os
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.quark_tv import QuarkTVDrive
from models import drive_store
from services import drives, setup_wizard

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def fake_login(monkeypatch):
    """模拟夸克电视版：第一次查询未确认，第二次确认并下发 refresh_token。"""
    state = {"polls": 0}

    def start_login(self):
        self._device_id()
        return {"qr": "QR", "state": {"query_token": "qt"}}

    def finish_login(self, login_state):
        assert login_state == {"query_token": "qt"}
        state["polls"] += 1
        if state["polls"] < 2:
            return False
        self.config["refresh_token"] = "secret-token"
        self.save(self.config)
        return True

    monkeypatch.setattr(QuarkTVDrive, "start_login", start_login)
    monkeypatch.setattr(QuarkTVDrive, "finish_login", finish_login)
    monkeypatch.setattr(QuarkTVDrive, "check", lambda self: "已连接")
    return state


def test_qr_login_saves_mount_without_exposing_token(fake_login):
    started = drives.qr_start("quark_tv", "夸克", create=True)
    assert started["qr"] == "data:image/jpeg;base64,QR" and started["login_id"]
    assert drives.qr_start("quark_tv", "另一个", create=True)["qr"] == "data:image/jpeg;base64,QR"
    assert drives.qr_poll(started["login_id"]) == {"ok": True, "pending": True}
    result = drives.qr_poll(started["login_id"])
    assert result["ok"] and "secret-token" not in json.dumps(result)
    row = drive_store.get_drive("夸克")
    assert row["kind"] == "quark_tv" and row["config"]["refresh_token"] == "secret-token"
    assert len(row["config"]["device_id"]) == 32
    # 用过的登录编号作废
    assert drives.qr_poll(started["login_id"])["expired"]


def test_qr_login_validation(fake_login):
    with pytest.raises(ValueError, match="不支持扫码"):
        drives.qr_start("quark", "夸克")
    drive_store.save_drive("夸克", "quark", {"cookie": "x"}, True)
    with pytest.raises(ValueError, match="已存在"):
        drives.qr_start("quark_tv", "夸克", create=True)
    with pytest.raises(ValueError, match="不能更改网盘类型"):
        drives.qr_start("quark_tv", "夸克")
    assert drives.qr_poll("unknown")["expired"]


def test_qr_login_route_and_form_markup(fake_login, tmp_path):
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    try:
        client = movie_app.app.test_client()
        started = client.post("/manager/drives/qr-login", json={"kind": "quark_tv", "mount": "夸克", "create": True},
                              headers=ORIGIN).get_json()
        assert started["ok"] and started["qr"].startswith("data:image/")
        bad = client.post("/manager/drives/qr-login", json={"kind": "local", "mount": "x"}, headers=ORIGIN)
        assert bad.status_code == 400
        page = client.get("/media?tab=drives", headers={"Accept": "text/html"}).get_data(as_text=True)
        assert 'data-drive-fields="quark_tv"' in page and "data-drive-qr-start" in page and "用夸克 App扫码" in page
        for kind in ("webdav", "onedrive", "gdrive", "pan115", "uc_tv"):
            assert f'<option value="{kind}">' in page
    finally:
        movie_app.app.config.update(previous)
