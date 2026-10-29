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
from models import drive_store
from services import env_file, setup_wizard

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
SAME_ORIGIN = {"Origin": "http://localhost"}
MANAGED = ("TMDB_TOKEN", "TMDB_PROXY", "TMDB_API_ORIGIN", "TMDB_IMAGE_ORIGIN",
           "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_RPM", "LLM_API", "LLM_CONCURRENCY", "MDBLIST_APIKEY", "FLASK_SECRET_KEY", "FFPROBE_PATH")
TOKEN = "eyJ" + "a" * 120


@pytest.fixture(autouse=True)
def tmdb_state(monkeypatch):
    """TMDB 选线是进程内状态，内置密钥随安装包而定：每个用例从“无内置、按配置选线”开始。"""
    from connectors import tmdb
    monkeypatch.setattr(tmdb, "BUILTIN_TOKEN", "")
    monkeypatch.delenv("TMDB_API_ORIGIN", raising=False)
    monkeypatch.delenv("TMDB_IMAGE_ORIGIN", raising=False)
    tmdb.use_origin(None)
    tmdb.clear_cache()
    yield tmdb
    tmdb.use_origin(None)


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
    drive_store.save_drive("local", "local", {"root": str(tmp_path)}, True)
    yield {"env": env_path, "db": database, "client": movie_app.app.test_client(), "tmp": tmp_path}
    movie_app.app.config.update(previous)
    movie_app.app.config.pop("LIBRARY_READY", None)
    for name in MANAGED:  # save() 会写入 os.environ
        os.environ.pop(name, None)


def _form(**values):
    return {"TMDB_TOKEN": TOKEN, **values}


def test_first_run_redirects_pages_to_setup(setup_env):
    client = setup_env["client"]
    response = client.get("/", headers=HTML)
    assert response.status_code == 302 and response.headers["Location"].endswith("/setup")
    # 接口请求与静态文件不拦截
    assert client.get("/static/style.css", headers=HTML).status_code == 200
    assert client.get("/manager/discovery", headers={"Accept": "application/json"}).status_code != 302
    page = client.get("/setup", headers=HTML)
    assert page.status_code == 200 and "欢迎" in page.get_data(as_text=True)


def test_setup_renders_drive_type_options(setup_env):
    page = setup_env["client"].get("/setup", headers=HTML)
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and 'id="setup-drive-kind"' in text
    for kind in ("local", "quark", "uc", "aliyun", "baidu", "pan123", "alist"):
        assert f'<option value="{kind}">' in text


def test_save_writes_env_creates_library_and_hides_secrets(setup_env):
    client = setup_env["client"]
    response = client.post("/setup/save", json=_form(TMDB_PROXY="http://127.0.0.1:7890"), headers=SAME_ORIGIN)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["ok"] and data["library_created"] and "add_root=1" in data["next"]
    assert "secret_key" not in data

    text = setup_env["env"].read_text(encoding="utf-8")
    assert f"TMDB_TOKEN={TOKEN}" in text and "ALIST_PASSWORD" not in text
    assert "TMDB_PROXY=http://127.0.0.1:7890" in text and "FLASK_SECRET_KEY=" in text
    assert "LLM_API_KEY" not in text.replace("# LLM_API_KEY=", "")
    with closing(sqlite3.connect(setup_env["db"])) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='title_basic'").fetchone()

    # 已就绪：不再重定向；状态接口只说明密钥已保存，不回显
    assert client.get("/media", headers=HTML).status_code == 200
    status = client.get("/setup/status").get_json()
    assert status["saved"]["TMDB_TOKEN"] is True and status["library"]["ready"] is True
    assert status["drives"][0]["mount"] == "local" and TOKEN not in str(status)

    # 再次保存时留空密钥 = 保持不变；已有媒体库不会被重建
    again = client.post("/setup/save", json=_form(TMDB_TOKEN="", mdblist_enabled=True,
                                                  MDBLIST_APIKEY="mdb"), headers=SAME_ORIGIN).get_json()
    assert again["ok"] and not again["library_created"]
    text = setup_env["env"].read_text(encoding="utf-8")
    assert "MDBLIST_APIKEY=mdb" in text and text.count("FLASK_SECRET_KEY=") == 1


def test_save_validates_required_and_ai_fields(setup_env):
    client = setup_env["client"]
    drive_store.delete_drive("local")
    missing = client.post("/setup/save", json=_form(), headers=SAME_ORIGIN)
    assert missing.status_code == 400 and "至少" in missing.get_json()["message"]
    drive_store.save_drive("local", "local", {"root": str(setup_env["tmp"])}, True)
    # 没有内置密钥的版本（源码运行）仍需要自己的 TMDB 密钥
    no_token = client.post("/setup/save", json=_form(TMDB_TOKEN=""), headers=SAME_ORIGIN)
    assert no_token.status_code == 400 and "内置" in no_token.get_json()["message"]
    ai = client.post("/setup/save", json=_form(ai_enabled=True, LLM_BASE_URL="https://x.test/v1"), headers=SAME_ORIGIN)
    assert ai.status_code == 400
    assert not setup_env["env"].exists() and not setup_env["db"].exists()


def test_save_with_builtin_token_needs_no_tmdb_key(setup_env, tmdb_state, monkeypatch):
    monkeypatch.setattr(tmdb_state, "BUILTIN_TOKEN", TOKEN)
    monkeypatch.setenv("LLM_API_KEY", "keep-me")
    tmdb_state.use_origin("https://api.tmdb.org/3")  # 检测时发现主域名连不上
    response = setup_env["client"].post("/setup/save", json=_form(TMDB_TOKEN="", image_proxy=True), headers=SAME_ORIGIN)
    assert response.status_code == 200, response.get_json()
    from dotenv import dotenv_values
    values = dotenv_values(setup_env["env"])
    assert "TMDB_TOKEN" not in values and TOKEN not in setup_env["env"].read_text(encoding="utf-8")
    assert values["TMDB_API_ORIGIN"] == "https://api.tmdb.org/3"
    assert values["TMDB_IMAGE_ORIGIN"] == tmdb_state.WSRV_IMAGE_ORIGIN
    # 引导不再包含 AI 与评分步骤：表单没带开关时不动已有配置
    assert os.environ["LLM_API_KEY"] == "keep-me"


def test_setup_rejects_cross_site_and_tunnel_requests(setup_env):
    client = setup_env["client"]
    assert client.post("/setup/save", json=_form(), headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.post("/setup/save", json=_form()).status_code == 403
    tunneled = client.post("/setup/test/tmdb", json=_form(), headers={**SAME_ORIGIN, "Cf-Connecting-Ip": "1.2.3.4"})
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


def test_local_drive_is_ready_for_scanning(setup_env):
    from services import media_source
    (setup_env["tmp"] / "movie.mkv").write_bytes(b"video")
    assert media_source.list_directory("/")[0]["name"] == "local"
    entries = media_source.list_directory("/local")
    assert ("movie.mkv", False) in [(entry["name"], entry["is_dir"]) for entry in entries]


def _image(status=200, kind="image/jpeg"):
    response = _response(status)
    response.headers = {"Content-Type": kind}
    return response


def test_tmdb_check(setup_env):
    v3 = "0123456789abcdef0123456789abcdef"
    with patch.object(requests, "get", return_value=_response(401)) as get:
        result = setup_wizard.test_tmdb({"TMDB_TOKEN": v3})
    assert not result["ok"] and "无效" in result["message"]
    # 32 位 API Key 按 v3 方式放在查询参数里，不发 Bearer
    assert get.call_args.kwargs["params"] == {"api_key": v3} and "Authorization" not in get.call_args.kwargs["headers"]
    with patch.object(requests, "get", side_effect=requests.ConnectTimeout()) as get:
        result = setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN})
        assert result["network"] and "代理" in result["message"]
        # 两个官方域名都试过
        assert [call.args[0] for call in get.call_args_list] == [
            "https://api.themoviedb.org/3/authentication", "https://api.tmdb.org/3/authentication"]
    with patch.object(requests, "get", side_effect=[_response(200), _response(200, {}), _response(200, {})]) as get:
        result = setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN, "TMDB_PROXY": "http://127.0.0.1:7890"})
        assert result["ok"] and result["source"] == "user" and result["image_ok"]
        assert get.call_args.kwargs["proxies"]["https"] == "http://127.0.0.1:7890"
        assert get.call_args_list[0].kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_tmdb_check_without_key_uses_builtin_or_asks(setup_env, tmdb_state, monkeypatch):
    result = setup_wizard.test_tmdb({})
    assert not result["ok"] and result["need_token"]
    monkeypatch.setattr(tmdb_state, "BUILTIN_TOKEN", TOKEN)
    with patch.object(requests, "get", return_value=_response(401)):
        result = setup_wizard.test_tmdb({})
    assert result["need_token"] and "内置" in result["message"]


def test_tmdb_check_falls_back_to_alternate_domain(setup_env, tmdb_state, monkeypatch):
    monkeypatch.setattr(tmdb_state, "BUILTIN_TOKEN", TOKEN)
    trending = {"results": [{"poster_path": "/AbCdEf1.jpg"}]}
    with patch.object(requests, "get", side_effect=[requests.ConnectionError(), _response(200), _response(200, trending),
                                                    _image(200, "text/html")]):
        result = setup_wizard.test_tmdb({"image_proxy": False})
    assert result["ok"] and result["source"] == "builtin" and result["origin"] == "https://api.tmdb.org/3"
    assert tmdb_state.api_origin() == "https://api.tmdb.org/3"
    # 图片加载失败：提示开启公共图片加速
    assert result["image_ok"] is False and result["warnings"]
    with patch.object(requests, "get", side_effect=[_response(200), _response(200, trending), _image()]) as get:
        result = setup_wizard.test_tmdb({"image_proxy": True})
    assert result["image_ok"] and get.call_args.args[0].startswith("https://wsrv.nl/?url=https://image.tmdb.org/t/p/w185/")


def test_tmdb_check_returns_trending_posters_for_onboarding(setup_env):
    trending = {"results": [{"poster_path": "/AbCdEf1.jpg"}, {"poster_path": None}, {"poster_path": "/../bad.jpg"},
                            {"poster_path": "/AbCdEf1.jpg"}, {"poster_path": "/XyZ_-99.png"}]}
    with patch.object(requests, "get", side_effect=[_response(200), _response(200, trending), _response(200, {}), _image()]) as get:
        result = setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN, "with_posters": True})
    assert result["ok"] and result["posters"] == ["AbCdEf1.jpg", "XyZ_-99.png"]
    assert get.call_args_list[1].args[0].endswith("/trending/all/week")
    with patch.object(requests, "get", return_value=_response(200)):
        assert "posters" not in setup_wizard.test_tmdb({"TMDB_TOKEN": TOKEN})


def test_tmdb_auth_and_domain_fallback(tmdb_state, monkeypatch):
    v3 = "0123456789abcdef0123456789abcdef"
    assert tmdb_state.auth(v3) == ({}, {"api_key": v3})
    assert tmdb_state.auth(TOKEN) == ({"Authorization": f"Bearer {TOKEN}"}, {})
    monkeypatch.setattr(tmdb_state.throttle, "call", lambda service, send, **kwargs: send())
    session = Mock()
    session.get.side_effect = [requests.ConnectionError(), _response(200, {"id": 1})]
    assert tmdb_state._get(session, "/movie/1", {}) == {"id": 1}
    assert session.get.call_args.args[0] == "https://api.tmdb.org/3/movie/1"
    assert tmdb_state.api_origin() == "https://api.tmdb.org/3"
    # 域名回退后继续复用响应缓存，不再请求；调用方修改结果也不会污染缓存。
    tmdb_state._get(session, "/movie/1", {})["id"] = 99
    assert tmdb_state._get(session, "/movie/1", {}) == {"id": 1}
    assert session.get.call_count == 2
    # 自定义镜像不切换，直接报错
    monkeypatch.setenv("TMDB_API_ORIGIN", "https://tmdb.mirror.test/3")
    session.get.side_effect = requests.ConnectionError()
    with pytest.raises(requests.ConnectionError):
        tmdb_state._get(session, "/movie/1", {})


def test_tmdb_builtin_token_is_fallback_and_images_skip_key(tmdb_state, monkeypatch, tmp_path):
    monkeypatch.delenv("TMDB_TOKEN", raising=False)
    monkeypatch.setattr(tmdb_state.paths, "ENV_PATH", tmp_path / ".env")
    assert tmdb_state.token_source() == ""
    with pytest.raises(RuntimeError):
        tmdb_state._session()
    v3 = "0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(tmdb_state, "BUILTIN_TOKEN", v3)
    assert tmdb_state.token_source() == "builtin"
    session = tmdb_state._session()
    assert session.params == {"api_key": v3} and "Authorization" not in session.headers
    monkeypatch.setenv("TMDB_TOKEN", TOKEN)
    assert tmdb_state.token_source() == "user" and tmdb_state._session().headers["Authorization"] == f"Bearer {TOKEN}"

    # 图片可能来自第三方 CDN：下载时去掉密钥
    response = Mock(status_code=200, headers={"Content-Type": "image/jpeg"})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b"jpeg"]
    session = Mock()
    session.get.return_value = response
    tmdb_state._download_image(session, "https://wsrv.nl/?url=https://image.tmdb.org/t/p/w185/a.jpg", tmp_path / "a.jpg")
    assert (tmp_path / "a.jpg").read_bytes() == b"jpeg"
    assert session.get.call_args.kwargs["headers"] == {"Authorization": None}
    assert session.get.call_args.kwargs["params"] == {"api_key": None}


def test_builtin_token_written_only_when_secret_present(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import build_windows

    monkeypatch.delenv("TMDB_BUILTIN_TOKEN", raising=False)
    assert build_windows.write_builtin_token(tmp_path) == 0
    assert not (tmp_path / "connectors" / "tmdb_builtin.py").exists()
    (tmp_path / "connectors").mkdir()
    monkeypatch.setenv("TMDB_BUILTIN_TOKEN", " abc'\"def ")
    assert build_windows.write_builtin_token(tmp_path) == 1
    namespace: dict = {}
    exec((tmp_path / "connectors" / "tmdb_builtin.py").read_text(encoding="utf-8"), namespace)
    assert namespace["TOKEN"] == "abc'\"def"


def test_zhipu_disables_thinking(monkeypatch):
    from connectors import llm
    for name in ("LLM_THINKING", "LLM_API"):
        monkeypatch.delenv(name, raising=False)
    settings = llm._settings({"LLM_BASE_URL": "https://open.bigmodel.cn/api/paas/v4", "LLM_MODEL": "glm-4.7-flash"})
    assert settings["provider"] == "zhipu" and settings["api"] == "chat"
    assert llm._chat_body(settings, "s", "u", None)["thinking"] == {"type": "disabled"}
    generic = llm._settings({"LLM_BASE_URL": "https://api.siliconflow.cn/v1", "LLM_MODEL": "m"})
    assert "thinking" not in llm._chat_body(generic, "s", "u", None)


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
