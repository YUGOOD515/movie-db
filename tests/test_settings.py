"""设置页：清单与 .env.example 一致、保存语义、立即生效、重启与安全限制。"""

import os
import re
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
import desktop
from connectors import tmdb
from services import settings, setup_wizard

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
SAME_ORIGIN = {"Origin": "http://localhost"}
TOKEN = "eyJ" + "a" * 120


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in (*settings.FIELDS, "FLASK_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALIST_USERNAME", "admin")
    monkeypatch.setenv("ALIST_PASSWORD", "old-pass")
    monkeypatch.setenv("TMDB_TOKEN", TOKEN)
    env_path = tmp_path / ".env"
    monkeypatch.setattr(setup_wizard, "ENV_PATH", env_path)
    monkeypatch.setattr(setup_wizard, "ffprobe_status", lambda: {"ok": False, "path": "", "bundled": False, "version": ""})
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY", "RESTART_HANDLER")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    movie_app.app.config.pop("RESTART_HANDLER", None)
    movie_app.app.config.pop("LIBRARY_READY", None)
    settings.clear_pending_restart()
    yield {"env": env_path, "client": movie_app.app.test_client()}
    movie_app.app.config.update(previous)
    movie_app.app.config.pop("LIBRARY_READY", None)
    settings.clear_pending_restart()
    for name in settings.FIELDS:  # save() 会写入 os.environ
        os.environ.pop(name, None)


def _save(client, values, **extra):
    return client.post("/settings/save", json={"values": values, **extra}, headers=SAME_ORIGIN)


def test_env_example_lists_every_setting():
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    missing = [name for name in settings.FIELDS if not re.search(rf"^#?\s*{name}=", example, re.M)]
    assert not missing


def test_settings_page_renders_registry_and_setup_redirects(env):
    client = env["client"]
    assert client.get("/setup", headers=HTML).headers["Location"].endswith("/settings")
    page = client.get("/settings", headers=HTML)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for section in settings.SECTIONS:
        assert f'data-tab="{section.id}"' in html
    for name in settings.FIELDS:
        assert f'name="{name}"' in html


def test_state_reports_secrets_as_saved_only(env):
    state = env["client"].get("/settings/state").get_json()
    assert state["saved"]["ALIST_PASSWORD"] is True and state["saved"]["LLM_API_KEY"] is False
    assert "old-pass" not in str(state) and TOKEN not in str(state)
    assert state["values"]["MOVIEDB_AUTOSCAN"] is True and state["restart_supported"] is False


def test_blank_secret_keeps_value_and_required_cannot_be_cleared(env):
    client = env["client"]
    response = _save(client, {"ALIST_USERNAME": "bob", "ALIST_PASSWORD": "", "TMDB_TOKEN": ""})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["changed"] == ["ALIST_USERNAME"]
    assert os.environ["ALIST_PASSWORD"] == "old-pass" and os.environ["TMDB_TOKEN"] == TOKEN
    text = env["env"].read_text(encoding="utf-8")
    assert "ALIST_USERNAME=bob" in text and TOKEN not in text

    refused = _save(client, {"ALIST_PASSWORD": None, "ALIST_USERNAME": ""})
    assert refused.status_code == 400
    assert set(refused.get_json()["errors"]) == {"ALIST_PASSWORD", "ALIST_USERNAME"}
    assert os.environ["ALIST_PASSWORD"] == "old-pass"


def test_tmdb_key_is_optional_and_status_shows_source(env, monkeypatch):
    from connectors import tmdb
    monkeypatch.setattr(tmdb, "BUILTIN_TOKEN", "")
    monkeypatch.setattr(tmdb.paths, "ENV_PATH", env["env"])
    assert settings._tmdb_status()["text"] == "自己的密钥"
    cleared = _save(env["client"], {"TMDB_TOKEN": None})
    assert cleared.status_code == 200 and "TMDB_TOKEN" not in os.environ
    assert settings._tmdb_status() == {"state": "warn", "text": "需要填写密钥"}
    monkeypatch.setattr(tmdb, "BUILTIN_TOKEN", "builtin")
    assert settings._tmdb_status() == {"state": "ok", "text": "内置密钥"}


def test_optional_secret_can_be_cleared(env, monkeypatch):
    monkeypatch.setenv("MDBLIST_APIKEY", "mdb")
    result = _save(env["client"], {"MDBLIST_APIKEY": None}).get_json()
    assert result["changed"] == ["MDBLIST_APIKEY"] and "MDBLIST_APIKEY" not in os.environ


def test_validation_errors_are_keyed_by_field(env):
    response = _save(env["client"], {"ALIST_ORIGIN": "127.0.0.1:5244", "LLM_BATCH_SIZE": "99",
                                     "FFPROBE_TIMEOUT": "abc", "LLM_API": "soap", "NOPE": "1"})
    assert response.status_code == 400
    errors = response.get_json()["errors"]
    assert set(errors) == {"ALIST_ORIGIN", "LLM_BATCH_SIZE", "FFPROBE_TIMEOUT", "LLM_API", "NOPE"}
    assert not env["env"].exists()


def test_bool_matching_default_is_not_written(env):
    client = env["client"]
    assert _save(client, {"MOVIEDB_AUTOSCAN": True}).get_json()["changed"] == []
    assert _save(client, {"MOVIEDB_AUTOSCAN": False}).get_json()["changed"] == ["MOVIEDB_AUTOSCAN"]
    assert os.environ["MOVIEDB_AUTOSCAN"] == "0"
    assert client.get("/settings/state").get_json()["values"]["MOVIEDB_AUTOSCAN"] is False


def test_ai_enable_requires_core_fields_and_normalizes_deepseek(env):
    client = env["client"]
    missing = _save(client, {"LLM_BASE_URL": "https://api.deepseek.com/v1"}, ai_enabled=True)
    assert missing.status_code == 400
    assert set(missing.get_json()["errors"]) == {"LLM_API_KEY", "LLM_MODEL"}

    result = _save(client, {"LLM_BASE_URL": "https://api.deepseek.com/v1", "LLM_API_KEY": "sk-1",
                            "LLM_MODEL": "deepseek-flash", "LLM_RPM": "30"}, ai_enabled=True).get_json()
    assert result["ok"]
    assert os.environ["LLM_BASE_URL"] == "https://api.deepseek.com" and "LLM_RPM" not in os.environ

    off = _save(client, {}, ai_enabled=False).get_json()
    assert set(off["changed"]) == {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"}
    assert not any(name in os.environ for name in settings.AI_CORE)


def test_alist_change_applies_immediately(env):
    with patch.object(settings.alist, "forget_token") as forget:
        result = _save(env["client"], {"ALIST_ORIGIN": "http://nas.local:5244/"}).get_json()
    forget.assert_called_once()
    assert "alist-login" in result["applied"] and "throttle" in result["applied"]
    assert movie_app.app.config["ALIST_ORIGIN"] == "http://nas.local:5244"


def test_port_change_waits_for_restart(env):
    client = env["client"]
    result = _save(client, {"MOVIEDB_PORT": "5099"}).get_json()
    assert result["restart"] == ["MOVIEDB_PORT"]
    assert client.get("/settings/state").get_json()["status"]["app"]["state"] == "warn"

    manual = client.post("/settings/restart", headers=SAME_ORIGIN).get_json()
    assert manual["ok"] is False and manual["manual"] is True

    handler = Mock(return_value="http://127.0.0.1:5099/settings")
    movie_app.app.config["RESTART_HANDLER"] = handler
    assert client.get("/settings/state").get_json()["restart_supported"] is True
    restarted = client.post("/settings/restart", headers=SAME_ORIGIN).get_json()
    assert restarted == {"ok": True, "url": "http://127.0.0.1:5099/settings"}
    handler.assert_called_once()


def test_writes_require_same_origin_and_local_access(env):
    client = env["client"]
    assert client.post("/settings/save", json={"values": {"ALIST_USERNAME": "x"}},
                       headers={"Origin": "http://evil.test"}).status_code == 403
    assert client.post("/settings/restart", headers={**SAME_ORIGIN, "Cf-Connecting-Ip": "1.2.3.4"}).status_code == 403
    assert client.get("/settings/state", headers={"Cf-Connecting-Ip": "1.2.3.4"}).status_code == 403
    assert os.environ["ALIST_USERNAME"] == "admin"


def test_setup_poster_validates_size_and_name(env):
    client = env["client"]
    with patch.object(setup_wizard.requests, "get") as get:
        assert client.get("/setup/poster/original/abcdef.jpg").status_code == 404
        assert client.get("/setup/poster/w342/..%2Fsecret.jpg").status_code == 404
        assert client.get("/setup/poster/w342/abc.gif").status_code == 404
        get.assert_not_called()
        get.return_value = Mock(status_code=200, headers={"Content-Type": "image/jpeg"}, content=b"jpg")
        response = client.get("/setup/poster/w342/AbCdEf123.jpg")
    assert response.status_code == 200 and response.data == b"jpg"
    assert get.call_args.args[0] == f"{tmdb.DEFAULT_IMAGE_ORIGIN}/w342/AbCdEf123.jpg"
    assert client.get("/setup/poster/w342/AbCdEf123.jpg", headers={"Cf-Connecting-Ip": "1.2.3.4"}).status_code == 404


def test_tmdb_origins_follow_environment(monkeypatch):
    monkeypatch.delenv("TMDB_API_ORIGIN", raising=False)
    assert tmdb.api_origin() == tmdb.DEFAULT_API_ORIGIN
    monkeypatch.setenv("TMDB_API_ORIGIN", "https://tmdb.example.com/3/")
    monkeypatch.setenv("TMDB_IMAGE_ORIGIN", "https://img.example.com/t/p")
    assert tmdb.api_origin() == "https://tmdb.example.com/3"
    assert tmdb.image_origin() == "https://img.example.com/t/p"


def test_relaunch_command_reuses_arguments_and_marks_restart():
    command = desktop.relaunch_command(["--port", "5099", "--no-browser", "--restarted"])
    script = next(index for index, part in enumerate(command) if part.endswith("desktop.py"))
    assert command[0] and command[1:script] == subprocess._args_from_interpreter_flags()
    assert command[script + 1:] == ["--port", "5099", "--no-browser", "--restarted"]
    assert desktop.relaunch_command(["--self-test"])[-1:] == ["--restarted"]
    assert "--self-test" not in desktop.relaunch_command(["--self-test"])
    # 重启后回到原来的页面；旧的 --open 不会叠加
    command = desktop.relaunch_command(["--open", "/manager", "--browser"], "/settings?x=1")
    assert command[-4:] == ["--browser", "--restarted", "--open", "/settings?x=1"]
    assert command.count("--open") == 1 and "/manager" not in command
    assert "--open" not in desktop.relaunch_command(["--open=/x"], "https://example.com/")
