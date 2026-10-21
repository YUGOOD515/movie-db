"""首次使用引导：重定向、测试连接、保存 .env 与建库。"""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from services import env_file, setup_wizard

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
SAME_ORIGIN = {"Origin": "http://localhost"}
MANAGED = ("ALIST_ORIGIN", "ALIST_USERNAME", "ALIST_PASSWORD", "ALIST_PATH_PASSWORD", "TMDB_TOKEN", "TMDB_PROXY",
           "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_RPM", "LLM_API", "LLM_CONCURRENCY", "MDBLIST_APIKEY", "FLASK_SECRET_KEY", "FFPROBE_PATH")
TOKEN = "eyJ" + "a" * 120


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    for name in MANAGED:
        monkeypatch.delenv(name, raising=False)
    env_path = tmp_path / ".env"
    monkeypatch.setattr(setup_wizard, "ENV_PATH", env_path)
    monkeypatch.setattr(setup_wizard, "ffprobe_status", lambda: {"ok": False, "path": "", "bundled": False, "version": ""})
    database = tmp_path / "library.db"
    previous = {key: movie_app.app.config.get(key) for key in ("DATABASE", "TESTING", "READ_ONLY")}
    movie_app.app.config.update(DATABASE=database, TESTING=True, READ_ONLY=False)
    movie_app.app.config.pop("LIBRARY_READY", None)
    yield {"env": env_path, "db": database, "client": movie_app.app.test_client()}
    movie_app.app.config.update(previous)
    movie_app.app.config.pop("LIBRARY_READY", None)
    for name in MANAGED:  # save() 会写入 os.environ
        os.environ.pop(name, None)


def _form(**values):
    return {"ALIST_ORIGIN": "http://127.0.0.1:5244", "ALIST_USERNAME": "admin", "ALIST_PASSWORD": "pa ss",
            "TMDB_TOKEN": TOKEN, **values}


def test_first_run_redirects_pages_to_setup(setup_env):
    client = setup_env["client"]
    response = client.get("/", headers=HTML)
    assert response.status_code == 302 and response.headers["Location"].endswith("/setup")
    # 接口请求与静态文件不拦截
    assert client.get("/static/style.css", headers=HTML).status_code == 200
    assert client.get("/manager/discovery", headers={"Accept": "application/json"}).status_code != 302
    page = client.get("/setup", headers=HTML)
    assert page.status_code == 200 and "欢迎" in page.get_data(as_text=True)


def test_save_writes_env_creates_library_and_hides_secrets(setup_env):
    client = setup_env["client"]
    response = client.post("/setup/save", json=_form(TMDB_PROXY="http://127.0.0.1:7890"), headers=SAME_ORIGIN)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["ok"] and data["library_created"] and "add_root=1" in data["next"]
    assert "secret_key" not in data

    text = setup_env["env"].read_text(encoding="utf-8")
    assert "ALIST_PASSWORD='pa ss'" in text and f"TMDB_TOKEN={TOKEN}" in text
    assert "TMDB_PROXY=http://127.0.0.1:7890" in text and "FLASK_SECRET_KEY=" in text
    assert "LLM_API_KEY" not in text.replace("# LLM_API_KEY=", "")
    with closing(sqlite3.connect(setup_env["db"])) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='title_basic'").fetchone()

    # 已就绪：不再重定向；状态接口只说明密钥已保存，不回显
    assert client.get("/manager", headers=HTML).status_code == 200
    status = client.get("/setup/status").get_json()
    assert status["saved"]["ALIST_PASSWORD"] is True and status["library"]["ready"] is True
    assert "pa ss" not in str(status) and TOKEN not in str(status)

    # 再次保存时留空密钥 = 保持不变；已有媒体库不会被重建
    again = client.post("/setup/save", json=_form(ALIST_PASSWORD="", TMDB_TOKEN="", mdblist_enabled=True,
                                                  MDBLIST_APIKEY="mdb"), headers=SAME_ORIGIN).get_json()
    assert again["ok"] and not again["library_created"]
    text = setup_env["env"].read_text(encoding="utf-8")
    assert "ALIST_PASSWORD='pa ss'" in text and "MDBLIST_APIKEY=mdb" in text and text.count("FLASK_SECRET_KEY=") == 1


def test_save_validates_required_and_ai_fields(setup_env):
    client = setup_env["client"]
    missing = client.post("/setup/save", json=_form(TMDB_TOKEN=""), headers=SAME_ORIGIN)
    assert missing.status_code == 400 and "必填" in missing.get_json()["message"]
    ai = client.post("/setup/save", json=_form(ai_enabled=True, LLM_BASE_URL="https://x.test/v1"), headers=SAME_ORIGIN)
    assert ai.status_code == 400
    assert not setup_env["env"].exists() and not setup_env["db"].exists()


def test_setup_rejects_cross_site_and_tunnel_requests(setup_env):
    client = setup_env["client"]
    assert client.post("/setup/save", json=_form(), headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.post("/setup/save", json=_form()).status_code == 403
    tunneled = client.post("/setup/test/alist", json=_form(), headers={**SAME_ORIGIN, "Cf-Connecting-Ip": "1.2.3.4"})
    assert tunneled.status_code == 403
    assert client.get("/setup/status", headers={"Cf-Connecting-Ip": "1.2.3.4"}).status_code == 403
    assert not setup_env["env"].exists()


def test_read_only_snapshot_has_no_setup(setup_env):
    movie_app.app.config["READ_ONLY"] = True
    client = setup_env["client"]
    assert client.get("/setup", headers=HTML).status_code in (404, 503)
    assert client.post("/setup/save", json=_form(), headers=SAME_ORIGIN).status_code in (404, 503)


def test_existing_invalid_database_is_not_overwritten(setup_env):
    setup_env["db"].write_bytes(b"not a database")
    response = setup_env["client"].post("/setup/save", json=_form(), headers=SAME_ORIGIN)
    assert response.status_code == 400 and "备份" in response.get_json()["message"]
    assert setup_env["db"].read_bytes() == b"not a database"


def test_env_file_update_keeps_comments_and_order(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# 注释\nA=1\nB=2\n# 说明\nB=3\nC=old\n", encoding="utf-8")
    env_file.update(path, {"B": "new value", "C": None, "D": "x#y"})
    assert path.read_text(encoding="utf-8") == "# 注释\nA=1\nB='new value'\n# 说明\n\nD='x#y'\n"
    from dotenv import dotenv_values
    assert dotenv_values(path) == {"A": "1", "B": "new value", "D": "x#y"}


def test_env_file_quotes_round_trip(tmp_path):
    from dotenv import dotenv_values
    path = tmp_path / ".env"
    values = {"S": "a'b\"c", "T": "with space", "U": "back\\slash"}
    env_file.update(path, values)
    assert dotenv_values(path) == values


def _response(status=200, payload=None):
    response = Mock(status_code=status)
    response.json.return_value = payload if payload is not None else {}
    return response


def test_alist_check_reports_sign_all_warning(setup_env):
    def post(url, **kwargs):
        if url.endswith("/api/auth/login"):
            return _response(payload={"code": 200, "data": {"token": "t"}})
        return _response(payload={"code": 200, "data": {"content": [{"name": "夸克"}]}})

    def get(url, **kwargs):
        if url.endswith("/api/public/settings"):
            return _response(payload={"data": {"version": "v3.45.0"}})
        return _response(payload={"code": 200, "data": {"value": "false"}})

    with patch.object(requests, "post", side_effect=post), patch.object(requests, "get", side_effect=get):
        result = setup_wizard.test_alist(_form())
    assert result["ok"] and result["sign_all"] is False
    assert "AList v3.45.0" in result["details"] and any("签名所有" in text for text in result["warnings"])

    with patch.object(requests, "post", return_value=_response(payload={"code": 400, "message": "password is incorrect"})):
        assert setup_wizard.test_alist(_form())["message"] == "用户名或密码不正确。"
    with patch.object(requests, "post", side_effect=requests.ConnectionError()):
        assert "AList 已启动" in setup_wizard.test_alist(_form())["message"]


def test_tmdb_check(setup_env):
    assert "32 位" in setup_wizard.test_tmdb({"TMDB_TOKEN": "0123456789abcdef0123456789abcdef"})["message"]
    with patch.object(requests, "get", return_value=_response(401)):
        assert not setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN})["ok"]
    with patch.object(requests, "get", side_effect=requests.ConnectTimeout()):
        result = setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN})
        assert result["network"] and "代理" in result["message"]
    with patch.object(requests, "get", return_value=_response(200)) as get:
        assert setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN, "TMDB_PROXY": "http://127.0.0.1:7890"})["ok"]
        assert get.call_args.kwargs["proxies"]["https"] == "http://127.0.0.1:7890"


def test_tmdb_check_returns_trending_posters_for_onboarding(setup_env):
    trending = {"results": [{"poster_path": "/AbCdEf1.jpg"}, {"poster_path": None}, {"poster_path": "/../bad.jpg"},
                            {"poster_path": "/AbCdEf1.jpg"}, {"poster_path": "/XyZ_-99.png"}]}
    with patch.object(requests, "get", side_effect=[_response(200), _response(200, trending), _response(200, {})]) as get:
        result = setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN, "with_posters": True})
    assert result["ok"] and result["posters"] == ["AbCdEf1.jpg", "XyZ_-99.png"]
    assert get.call_args_list[1].args[0].endswith("/trending/all/week")
    with patch.object(requests, "get", return_value=_response(200)):
        assert "posters" not in setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN})


def test_tmdb_session_uses_proxy_only_when_configured(monkeypatch):
    from connectors import tmdb
    monkeypatch.setenv("TMDB_TOKEN", TOKEN)
    monkeypatch.delenv("TMDB_PROXY", raising=False)
    assert not tmdb._session().proxies
    monkeypatch.setenv("TMDB_PROXY", "http://127.0.0.1:7890")
    assert tmdb._session().proxies["https"] == "http://127.0.0.1:7890"


def test_llm_check_flags_unknown_model(setup_env):
    payload = {"data": [{"id": "glm-4-flash"}, {"id": "glm-4-plus"}]}
    form = {"LLM_BASE_URL": "https://open.bigmodel.cn/api/paas/v4", "LLM_API_KEY": "k", "LLM_MODEL": "glm-4-flash"}
    answer = {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 20, "completion_tokens": 5}}
    with patch.object(requests, "get", return_value=_response(200, payload)), \
         patch.object(requests, "post", return_value=_response(200, answer)) as post:
        result = setup_wizard.test_llm(form)
        assert result["ok"] and not result["warnings"]
        assert result["details"][0].startswith("Chat Completions")
        assert post.call_args.args[0] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        assert setup_wizard.test_llm({**form, "LLM_MODEL": "GLM-4"})["warnings"]
    with patch.object(requests, "get", return_value=_response(401)):
        assert not setup_wizard.test_llm(form)["ok"]


def test_llm_check_deepseek_uses_responses_without_thinking(setup_env):
    form = {"LLM_BASE_URL": "https://api.deepseek.com/v1", "LLM_API_KEY": "k", "LLM_MODEL": "deepseek-flash"}
    answer = {"status": "completed", "output_text": '{"ok": true}', "usage": {"input_tokens": 20, "output_tokens": 5}}
    with patch.object(requests, "get", return_value=_response(200, {"data": [{"id": "deepseek-flash"}]})), \
         patch.object(requests, "post", return_value=_response(200, answer)) as post:
        result = setup_wizard.test_llm(form)
    assert result["ok"] and "已关闭思考模式，按并发限流" in result["details"]
    assert post.call_args.args[0] == "https://api.deepseek.com/responses"
    body = post.call_args.kwargs["json"]
    assert body["reasoning"] == {"effort": "none"} and body["text"] == {"format": {"type": "json_object"}}
    with patch.object(requests, "get", return_value=_response(200, {"data": []})), \
         patch.object(requests, "post", return_value=_response(402, {"error": {"message": "Insufficient Balance"}})):
        result = setup_wizard.test_llm(form)
    assert not result["ok"] and "余额不足" in result["message"]


def test_unknown_test_service(setup_env):
    response = setup_env["client"].post("/setup/test/nope", json={}, headers=SAME_ORIGIN)
    assert response.get_json() == {"ok": False, "message": "未知的测试项。"}


def test_desktop_launcher_helpers(tmp_path):
    import socket
    import desktop

    icon = desktop.make_icon(64)
    assert icon.size == (64, 64) and icon.getpixel((32, 32))[3] == 255
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert desktop._running_instance(f"http://127.0.0.1:{free_port}") is None


def test_uninstall_list_keeps_user_data(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import build_windows

    app = tmp_path / "app"
    (app / "services").mkdir(parents=True)
    (app / "app.py").write_text("")
    (app / "services" / "x.py").write_text("")
    text = build_windows.write_uninstall_list(app, tmp_path / "list.nsh").read_text(encoding="utf-8")
    assert 'Delete "$INSTDIR\\app\\services\\x.py"' in text and 'RMDir "$INSTDIR\\app\\services"' in text
    assert "/r \"$INSTDIR\\app\\services\"" not in text and "library.db" not in text
