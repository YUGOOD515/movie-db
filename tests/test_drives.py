import os
from pathlib import Path
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.base import DriveError
from models import drive_store
from services import drives, setup_wizard

ORIGIN = {"Origin": "http://localhost"}
HTML = {"Accept": "text/html"}


@pytest.fixture
def env(tmp_path):
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    yield {"client": movie_app.app.test_client(), "tmp": tmp_path}
    movie_app.app.config.update(previous)


def save(client, mount="local", kind="local", config=None, create=None):
    payload = {"mount": mount, "kind": kind, "config": config or {}}
    if create is not None:
        payload["create"] = create
    return client.post("/manager/drives/save", json=payload, headers=ORIGIN)


def test_local_drive_crud_page_and_json_are_safe(env):
    response = save(env["client"], config={"root": str(env["tmp"])})
    assert response.status_code == 200 and response.get_json()["ok"]
    assert set(response.get_json()) == {"ok", "status"}
    assert drive_store.get_drive("local")["status"] == f"已连接：{env['tmp']}"

    redirect = env["client"].get("/manager/drives", headers=HTML)
    assert redirect.status_code == 302 and redirect.headers["Location"].endswith("/media?tab=drives")
    page = env["client"].get("/media?tab=drives", headers=HTML)
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "本机目录" in text and "AList / OpenList（透传）" in text and "local" in text
    assert "settings-row" in text and "settings-advanced" in text and "settings-status" in text
    assert "data-drive-reveal" in text and "data-secret-textarea" in text and 'data-pick-path="folder"' in text
    kinds = ("local", "quark", "uc", "aliyun", "baidu", "pan123", "alist")
    positions = [text.index(f'<option value="{kind}">') for kind in kinds]
    assert positions == sorted(positions)

    assert env["client"].post("/manager/drives/local/test", headers=ORIGIN).get_json()["ok"]
    toggled = env["client"].post("/manager/drives/local/toggle", headers=ORIGIN).get_json()
    assert toggled["enabled"] is False and drive_store.get_drive("local")["enabled"] is False
    assert env["client"].post("/manager/drives/local/delete", headers=ORIGIN).get_json()["ok"]
    assert drive_store.get_drive("local") is None


def test_drives_tab_does_not_require_library(env):
    (env["tmp"] / "library.db").unlink()
    response = env["client"].get("/media?tab=drives")
    assert response.status_code == 200 and "data-drive-page" in response.get_data(as_text=True)


def test_existing_passthrough_is_hidden_from_type_options(env):
    drive_store.save_drive("*", "alist", {}, True)
    text = env["client"].get("/media?tab=drives", headers=HTML).get_data(as_text=True)
    assert '<option value="alist">' not in text


def test_association_without_drives_links_to_drives_tab(env):
    text = env["client"].get("/media?tab=associate", headers=HTML).get_data(as_text=True)
    assert "尚未添加网盘" in text and 'href="/media?tab=drives"' in text


def test_read_only_snapshot_hides_drives_tab(env):
    movie_app.app.config["READ_ONLY"] = True
    response = env["client"].get("/media?tab=drives", headers=HTML)
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'href="/media?tab=drives"' not in text and "data-drive-page" not in text


def test_secret_stays_on_edit_and_failed_check_does_not_save(env, monkeypatch):
    class FakeDriver:
        def __init__(self, config, save):
            self.config = config

        def check(self):
            if self.config.get("origin") == "bad":
                raise DriveError("检测失败")
            return "已连接"

    monkeypatch.setattr(drives, "_driver", lambda kind, config, save: FakeDriver(config, save))
    assert save(env["client"], "ignored", "alist", {"origin": "old", "username": "u", "password": "secret"}).status_code == 200
    row = drive_store.get_drive("*")
    assert row["config"]["password"] == "secret"
    assert "secret" not in str(drives.listing())

    assert save(env["client"], "ignored", "alist", {"origin": "new", "username": "u", "password": ""}).status_code == 200
    row = drive_store.get_drive("*")
    assert row["config"]["origin"] == "new" and row["config"]["password"] == "secret"

    response = save(env["client"], "other", "local", {"root": "bad"})
    assert response.status_code == 200  # 本机驱动不参与这次假驱动替换
    drive_store.delete_drive("other")
    response = save(env["client"], "ignored", "alist", {"origin": "bad", "username": "u", "password": "secret"})
    assert response.status_code == 400 and response.get_json()["ok"] is False
    assert drive_store.get_drive("*")["config"]["origin"] == "new"


def test_missing_mounts_use_first_path_segment_and_fallback():
    paths = ["/夸克/电影/a.mkv", "/夸克/电影/b.mkv", "/UC/a.mkv"]
    assert drives.missing_mounts(paths) == ["UC", "夸克"]
    drive_store.save_drive("夸克", "local", {"root": "/"}, True)
    assert drives.missing_mounts(paths) == ["UC"]
    drive_store.save_drive("*", "alist", {}, True)
    assert drives.missing_mounts(paths) == []


def test_create_rejects_existing_mount_without_overwriting(env):
    root = str(env["tmp"])
    assert save(env["client"], "duplicate", config={"root": root}, create=True).get_json()["ok"]
    response = save(env["client"], "duplicate", config={"root": "changed"}, create=True)
    assert response.status_code == 400
    assert response.get_json()["message"] == "挂载名已存在，请换一个或编辑原挂载"
    assert drive_store.get_drive("duplicate")["config"]["root"] == root
    drive_store.delete_drive("duplicate")


def test_edit_preserves_disabled_state(env):
    assert save(env["client"], "disabled", config={"root": str(env["tmp"])}, create=True).get_json()["ok"]
    assert env["client"].post("/manager/drives/disabled/toggle", headers=ORIGIN).get_json()["enabled"] is False
    response = save(env["client"], "disabled", config={"root": str(env["tmp"])}, create=False)
    assert response.status_code == 200 and response.get_json()["ok"]
    assert drive_store.get_drive("disabled")["enabled"] is False
    drive_store.delete_drive("disabled")


def test_cross_origin_error_asks_for_page_refresh(env):
    response = env["client"].post("/manager/drives/save", json={}, headers={"Origin": "https://evil.test"})
    assert response.status_code == 403
    assert response.get_json()["message"] == "请求来源不正确，请刷新页面后重试。"


def test_media_source_fields_use_mount_path_guidance(env):
    text = env["client"].get("/media?tab=associate", headers=HTML).get_data(as_text=True)
    assert "AList 视频直链" not in text and "alist_origin" not in text and "/d/..." not in text
    assert text.count("挂载路径或直链，如 /夸克/电影/xxx.mkv") == 8
    assert 'type="text" aria-label="挂载路径或直链，如 /夸克/电影/xxx.mkv"' in text
    assert 'id="search-alist-url" type="text"' in text


def test_drive_editor_responsive_markup_and_script_contract(env):
    text = env["client"].get("/media?tab=drives", headers=HTML).get_data(as_text=True)
    assert 'col-12 col-xl-5 order-2 order-xl-1' in text
    assert 'col-12 col-xl-7 order-1 order-xl-2' in text
    assert 'vstack drive-list' in text and 'data-drive-editor-icon' in text
    script = (Path(movie_app.app.static_folder) / "drives.js").read_text(encoding="utf-8")
    assert "create: !editingMount" in script
    assert ': input.dataset.drivePlaceholder || ""' in script
    assert "mountInput.disabled = true" in script
    assert 'replaceChildren(icon("pencil"))' in script
