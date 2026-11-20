"""真实关联映射与合并入口的回归检查，不依赖网盘在线或扫描成功。"""

import sqlite3
from contextlib import closing

import pytest

import app as movie_app
from models import local_store
from services import setup_wizard


@pytest.fixture
def workspace(tmp_path):
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    local_store.upgrade(database)
    with closing(sqlite3.connect(database)) as db:
        db.executescript((movie_app.BASE_DIR / "tests/fixtures/library_fixture.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO media_file (title_id, alist_path) VALUES (?, ?)",
                   ("tt6719334", "/Other/Young Sheldon/Pilot.2160p.mkv"))
        db.execute("UPDATE title_relation SET season_number = 0 WHERE episode_id = ?", ("tt6719334",))
        db.commit()
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    movie_app.app.config.pop("LIBRARY_READY", None)
    yield movie_app.app.test_client(), database
    movie_app.app.config.update(previous)
    movie_app.app.config.pop("LIBRARY_READY", None)


def test_linked_files_use_library_identity_and_keep_multiple_sources(workspace):
    client, database = workspace
    before = database.read_bytes()
    response = client.get("/manager/association/linked")
    assert response.status_code == 200
    records = {record["title_id"]: record for record in response.json["records"]}
    assert set(records) == {"tt0816692", "tt6226232"}
    series = records["tt6226232"]
    assert series["media_type"] == "tv" and series["tmdb_id"] == 71728
    assert len(series["files"]) == 6
    pilots = [file for file in series["files"] if file["title_id"] == "tt6719334"]
    assert len(pilots) == 2 and all(file["position"] == "S00E01" for file in pilots)
    assert {file["path"].split("/")[1] for file in pilots} == {"TV", "Other"}
    assert records["tt0816692"]["files"][0]["position"] == "电影"
    assert series["detail_url"] == "/title/tt6226232"
    # 已入库的作品用本地海报，没有本地海报时留空（前端再按 TMDB ID 现查）
    assert records["tt0816692"]["poster"] == "/static/imdb.png"
    assert client.get("/manager/association/linked", headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
    assert database.read_bytes() == before


def test_linked_files_follow_snapshot_permission_boundary(workspace):
    client, _ = workspace
    movie_app.app.config["READ_ONLY"] = True
    response = client.get("/manager/association/linked")
    assert response.status_code == 503 and response.json["read_only"]


def test_old_drive_url_keeps_all_actions_in_association(workspace):
    client, _ = workspace
    redirect = client.get("/media?tab=drives&source=/A")
    assert redirect.status_code == 302 and redirect.headers["Location"].endswith("/media/sources?source=/A")
    response = client.get("/media/sources")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'href="/media?tab=drives"' not in text
    for feature in ("data-drive-add", "data-drive-import", "data-drive-modal", "data-source-ignored",
                    "data-linked-editor", "data-discovery-refresh", 'id="title-picker"'):
        assert feature in text
    # 手动录入和“管理扫描目录”已去掉：目录在左栏和连接详情里管理
    for removed in ("data-manual-open", "data-batch-import-form", "data-discovery-roots", "data-source-heading"):
        assert removed not in text
    assert 'media-association.js' in text and 'drives.js' in text
