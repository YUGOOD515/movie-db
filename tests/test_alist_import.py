import json
import os
import sqlite3
from contextlib import closing
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.alist_config import read_storages
from connectors.drives.quark import QuarkDrive
from models import drive_store
from services import alist_import, setup_wizard

ORIGIN = {"Origin": "http://localhost"}


def alist_database(path, storages):
    """按 AList v3 的 x_storages 结构建一个最小数据库。"""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE x_storages (id INTEGER PRIMARY KEY, mount_path TEXT, "order" INTEGER,
                      driver TEXT, addition TEXT, disabled NUMERIC, status TEXT)""")
        for index, (mount, driver, addition, disabled) in enumerate(storages):
            db.execute('INSERT INTO x_storages (mount_path, "order", driver, addition, disabled, status) '
                       "VALUES (?, ?, ?, ?, ?, 'work')", (mount, index, driver, json.dumps(addition), int(disabled)))
    return path


@pytest.fixture
def source(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    return alist_database(tmp_path / "data.db", [
        ("/Quark", "Quark", {"cookie": "__pus=p; __puus=s", "root_folder_id": "0"}, False),
        ("/本机", "Local", {"root_folder_path": str(media)}, False),
        ("/115", "115 Cloud", {"cookie": "UID=1"}, False),
        ("/a/b", "Local", {"root_folder_path": str(media)}, False),
        ("/旧盘", "Quark", {"cookie": "x"}, True),
    ])


def test_reads_database_and_backup_json(source, tmp_path):
    storages = read_storages(str(source))
    assert [item["mount_path"] for item in storages] == ["/Quark", "/本机", "/115", "/a/b", "/旧盘"]
    assert storages[0]["addition"]["cookie"] == "__pus=p; __puus=s" and storages[4]["disabled"]

    backup = tmp_path / "backup.json"
    backup.write_text(json.dumps({"storages": [{"mount_path": "/Quark", "driver": "Quark",
                                                "addition": json.dumps({"cookie": "c"}), "disabled": False}]}),
                      encoding="utf-8")
    assert read_storages(str(backup))[0]["addition"] == {"cookie": "c"}

    other = tmp_path / "other.db"
    sqlite3.connect(other).close()
    with pytest.raises(ValueError, match="存储表"):
        read_storages(str(other))
    with pytest.raises(ValueError, match="找不到"):
        read_storages(str(tmp_path / "missing.db"))


def test_preview_plans_each_storage_without_exposing_credentials(source):
    drive_store.save_drive("*", "alist", {"origin": "http://127.0.0.1:5244", "username": "u", "password": "p"}, True)
    drive_store.save_drive("Quark", "quark", {"cookie": "old"}, False)
    drive_store.set_delegated("Quark", True)

    result = alist_import.preview(str(source))
    items = {item["mount_path"]: item for item in result["items"]}
    assert result["importable"] == 2
    assert items["/Quark"]["action"] == "update" and "改回由本网盘提供" in items["/Quark"]["reason"]
    assert items["/本机"]["action"] == "create" and items["/本机"]["kind"] == "local"
    assert items["/115"]["action"] == "skip" and "默认（透传）" in items["/115"]["reason"]
    assert items["/a/b"]["action"] == "skip" and "一级挂载名" in items["/a/b"]["reason"]
    assert items["/旧盘"]["action"] == "skip" and "已停用" in items["/旧盘"]["reason"]
    assert "config" not in json.dumps(result) and "__puus" not in json.dumps(result)


def test_apply_updates_credentials_and_reclaims_delegated_mount(source, monkeypatch):
    monkeypatch.setattr(QuarkDrive, "check", lambda self: "已连接")
    drive_store.save_drive("*", "alist", {"origin": "http://127.0.0.1:5244", "username": "u", "password": "p"}, True)
    drive_store.save_drive("Quark", "quark", {"cookie": "old", "root_id": "0"}, False)
    drive_store.set_delegated("Quark", True)

    result = alist_import.apply(str(source), ["Quark", "本机", "115"])
    assert result["ok"] and [item["mount"] for item in result["results"]] == ["Quark", "本机"]
    quark = drive_store.get_drive("Quark")
    assert quark["config"]["cookie"] == "__pus=p; __puus=s"
    assert quark["enabled"] and not quark["delegated"]
    assert drive_store.get_drive("本机")["kind"] == "local"
    assert "Quark" in result["rerouted"] and result["route_message"]
    assert result["alist_unused"] is False  # /115 与 /a/b 仍需要 AList


def test_import_route_previews_and_rejects_bad_path(source, tmp_path):
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    try:
        client = movie_app.app.test_client()
        preview = client.post("/manager/drives/import-alist", json={"path": str(source)}, headers=ORIGIN)
        assert preview.status_code == 200 and len(preview.get_json()["items"]) == 5
        missing = client.post("/manager/drives/import-alist", json={"path": str(tmp_path / "none.db")}, headers=ORIGIN)
        assert missing.status_code == 400 and not missing.get_json()["ok"]
        remote = client.post("/manager/drives/import-alist", json={"path": str(source)},
                             headers={**ORIGIN, "Cf-Connecting-Ip": "203.0.113.1"})
        assert remote.status_code == 403
        page = client.get("/media?tab=drives", headers={"Accept": "text/html"}).get_data(as_text=True)
        assert "data-drive-import-modal" in page and "从 AList 导入" in page
    finally:
        movie_app.app.config.update(previous)


def test_maps_new_drivers_and_skips_sharepoint(tmp_path):
    source = alist_database(tmp_path / "more.db", [
        ("/电视夸克", "QuarkTV", {"refresh_token": "rt", "device_id": "dev"}, False),
        ("/NAS", "WebDav", {"address": "https://nas/dav", "username": "u", "password": "p",
                            "root_folder_path": "/影视", "vendor": "other"}, False),
        ("/SP", "WebDav", {"address": "https://x.sharepoint.com", "vendor": "sharepoint"}, False),
        ("/OD", "Onedrive", {"refresh_token": "rt", "client_id": "c", "client_secret": "s", "region": "global",
                             "redirect_uri": "https://alistgo.com/tool/onedrive/callback"}, False),
        ("/GD", "GoogleDrive", {"refresh_token": "rt", "use_online_api": True,
                                "api_url_address": "https://api.oplist.org/googleui/renewapi"}, False),
        ("/115", "115 Open", {"refresh_token": "rt", "access_token": "at", "root_folder_id": "0"}, False),
    ])
    plans = {item["mount"]: item for item in alist_import._plans(str(source))}
    assert {mount: item["kind"] for mount, item in plans.items()} == {
        "电视夸克": "quark_tv", "NAS": "webdav", "SP": "webdav", "OD": "onedrive", "GD": "gdrive", "115": "pan115"}
    assert plans["NAS"]["config"] == {"url": "https://nas/dav", "username": "u", "password": "p", "root_path": "/影视"}
    assert plans["SP"]["action"] == "skip" and "SharePoint" in plans["SP"]["reason"]
    assert plans["OD"]["config"]["client_id"] == "c" and "renew_api" not in plans["OD"]["config"]
    assert plans["GD"]["config"]["renew_api"].endswith("googleui/renewapi") and "client_id" not in plans["GD"]["config"]
    assert plans["电视夸克"]["config"]["device_id"] == "dev"
    assert all(item["action"] == "create" for mount, item in plans.items() if mount != "SP")
