"""首次使用引导：检测配置、逐项测试外部服务、保存到 .env 并初始化媒体库。

页面只拿到“某项密钥是否已保存”，不会拿到密钥本身；测试与保存时留空的密钥沿用已保存的值。
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import requests

from connectors import alist, llm, throttle, tmdb
from services import env_file

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
SCHEMA_PATH = ROOT / "migrations" / "library_schema.sql"
BUNDLED_FFPROBE = ROOT.parent / "runtime" / "ffmpeg" / "ffprobe.exe"
TIMEOUT = 12

SECRET_FIELDS = ("ALIST_PASSWORD", "ALIST_PATH_PASSWORD", "TMDB_TOKEN", "LLM_API_KEY", "MDBLIST_APIKEY")
PLAIN_FIELDS = ("ALIST_ORIGIN", "ALIST_USERNAME", "TMDB_PROXY", "LLM_BASE_URL", "LLM_MODEL", "LLM_RPM")

# OpenAI 兼容接口的常见平台；model 只作输入提示。
LLM_PRESETS = [
    # DeepSeek 官方地址不带 /v1；默认走 Responses API、关闭思考；只限并发不限每分钟次数（rpm=False 隐藏该项）。
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
     "key_url": "https://platform.deepseek.com/api_keys", "rpm": False},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen3-8B",
     "key_url": "https://cloud.siliconflow.cn/account/ak"},
    {"id": "zhipu", "name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash",
     "key_url": "https://open.bigmodel.cn/usercenter/apikeys"},
    {"id": "gemini", "name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-2.5-flash", "key_url": "https://aistudio.google.com/apikey"},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "model": "deepseek/deepseek-chat-v3-0324:free", "key_url": "https://openrouter.ai/settings/keys"},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
     "key_url": "https://platform.openai.com/api-keys"},
]


class SetupError(ValueError):
    pass


# ---------------------------------------------------------------- 状态


def library_ready(database: Path) -> bool:
    """媒体库是否已建好：文件存在且有主表。空文件或缺表都视为首次使用。"""
    database = Path(database)
    if not database.is_file() or database.stat().st_size == 0:
        return False
    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
            return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='title_basic'").fetchone() is not None
    except sqlite3.Error:
        return False


def ensure_library(database: Path) -> bool:
    """首次使用时按建表脚本创建空媒体库；已有媒体库绝不改动。返回是否新建。"""
    database = Path(database)
    if library_ready(database):
        return False
    if database.exists() and database.stat().st_size > 0:
        raise SetupError(f"{database.name} 已存在但不是有效的媒体库，请先移走或备份该文件。")
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db:
        db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return True


def ffprobe_status() -> dict:
    configured = os.environ.get("FFPROBE_PATH", "").strip()
    candidates = [configured] if configured and configured != "ffprobe" else []
    if BUNDLED_FFPROBE.is_file():
        candidates.append(str(BUNDLED_FFPROBE))
    found = shutil.which("ffprobe")
    if found:
        candidates.append(found)
    for candidate in candidates:
        try:
            result = subprocess.run([candidate, "-version"], capture_output=True, text=True, timeout=8,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            first = (result.stdout.splitlines() or [""])[0]
            match = re.search(r"ffprobe version (\S+)", first)
            return {"ok": True, "path": candidate, "bundled": Path(candidate) == BUNDLED_FFPROBE,
                    "version": match.group(1) if match else ""}
    return {"ok": False, "path": "", "bundled": False, "version": ""}


def current(database: Path) -> dict:
    """引导页初始数据：普通字段给值，密钥只给“是否已保存”。"""
    values = {name: os.environ.get(name, "") for name in PLAIN_FIELDS}
    values["ALIST_ORIGIN"] = values["ALIST_ORIGIN"] or "http://127.0.0.1:5244"
    return {
        "values": values,
        "saved": {name: bool(os.environ.get(name, "").strip()) for name in SECRET_FIELDS},
        "library": {"ready": library_ready(database), "name": Path(database).name, "folder": str(Path(database).parent)},
        "ffprobe": ffprobe_status(),
        "presets": LLM_PRESETS,
        "env_exists": ENV_PATH.exists(),
        "alist_running": _alist_running(values["ALIST_ORIGIN"]),
    }


def _alist_running(origin: str) -> bool:
    from services.alist_process import is_alist_running

    try:
        return is_alist_running(origin)
    except ValueError:
        return False


# ---------------------------------------------------------------- 测试


def _field(form: dict, name: str) -> str:
    value = form.get(name)
    if not isinstance(value, str):
        value = ""
    value = value.strip()
    if not value and name in SECRET_FIELDS:
        value = os.environ.get(name, "").strip()
    return value


def _url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SetupError(f"{label}需要以 http:// 或 https:// 开头。")
    return value.rstrip("/")


def _network_message(error: Exception, what: str) -> str:
    if isinstance(error, requests.Timeout):
        return f"连接{what}超时。"
    if isinstance(error, requests.exceptions.SSLError):
        return f"与{what}的安全连接失败。"
    return f"无法连接{what}。"


def test_alist(form: dict) -> dict:
    origin = _url(_field(form, "ALIST_ORIGIN") or "http://127.0.0.1:5244", "AList 地址")
    username, password = _field(form, "ALIST_USERNAME"), _field(form, "ALIST_PASSWORD")
    if not username or not password:
        raise SetupError("请填写 AList 用户名和密码。")
    try:
        login = requests.post(origin + "/api/auth/login", json={"username": username, "password": password}, timeout=TIMEOUT)
        payload = login.json()
    except ValueError:
        return {"ok": False, "message": "这个地址不是 AList 服务，请检查端口。"}
    except requests.RequestException as error:
        return {"ok": False, "message": _network_message(error, " AList") + "请确认 AList 已启动、地址和端口正确。"}
    if payload.get("code") != 200:
        return {"ok": False, "message": "用户名或密码不正确。" if payload.get("code") in (400, 401, 403)
                else f"AList 登录失败：{payload.get('message') or '未知错误'}"}
    token = payload["data"]["token"]
    headers = {"Authorization": token}
    details, warnings = [], []
    try:
        version = requests.get(origin + "/api/public/settings", timeout=TIMEOUT).json().get("data", {}).get("version")
        if version:
            details.append(f"AList {version}")
    except (requests.RequestException, ValueError, AttributeError):
        pass
    try:
        listing = requests.post(origin + "/api/fs/list", headers=headers, timeout=TIMEOUT, json={
            "path": "/", "password": _field(form, "ALIST_PATH_PASSWORD"), "page": 1, "per_page": 0, "refresh": False,
        }).json()
        entries = (listing.get("data") or {}).get("content") or []
        details.append(f"根目录有 {len(entries)} 个存储或文件夹" if entries else "根目录是空的")
        if not entries:
            warnings.append("这个账号在根目录看不到任何存储，请检查账号的基本路径和权限。")
    except (requests.RequestException, ValueError):
        warnings.append("登录成功，但读取根目录失败。")
    sign = None
    try:
        setting = requests.get(origin + "/api/admin/setting/get", headers=headers, params={"key": "sign_all"},
                               timeout=TIMEOUT).json()
        if setting.get("code") == 200:
            sign = str((setting.get("data") or {}).get("value")).lower() == "true"
    except (requests.RequestException, ValueError):
        pass
    if sign is False:
        warnings.append("“签名所有”未开启：导入时拿不到带签名的播放链接。请在 AList 后台“设置 → 全局”中打开。")
    elif sign is None:
        warnings.append("当前账号不是管理员，无法检查“签名所有”；导入前请确认 AList 后台“设置 → 全局”中已开启。")
    return {"ok": True, "message": "已连接", "details": details, "warnings": warnings, "sign_all": sign}


def test_tmdb(form: dict) -> dict:
    token = _field(form, "TMDB_TOKEN")
    if not token:
        raise SetupError("请填写 TMDB 的 API 读访问令牌。")
    if len(token) < 64 and re.fullmatch(r"[0-9a-f]{32}", token):
        return {"ok": False, "message": "这是 32 位的 API Key，这里需要更长的“API 读访问令牌”（API Read Access Token）。"}
    proxy = _field(form, "TMDB_PROXY")
    proxies = {"http": _url(proxy, "代理地址"), "https": _url(proxy, "代理地址")} if proxy else None
    origin = tmdb.api_origin()
    try:
        response = requests.get(origin + "/authentication", headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                                proxies=proxies, timeout=TIMEOUT)
    except requests.RequestException as error:
        return {"ok": False, "network": True,
                "message": _network_message(error, " TMDB") + ("请检查代理地址是否可用。" if proxy else "国内网络可能需要代理，填写代理地址后重试。")}
    if response.status_code == 401:
        return {"ok": False, "message": "令牌无效，请复制完整的“API 读访问令牌”。"}
    if response.status_code != 200:
        return {"ok": False, "message": f"TMDB 返回错误（HTTP {response.status_code}），请稍后重试。"}
    result = {"ok": True, "message": "已连接", "details": ["令牌有效"] + (["通过代理访问"] if proxy else [])}
    if form.get("with_posters"):
        result["posters"] = trending_posters(origin, token, proxies)
    return result


# 引导页海报墙：TMDB 本周热门的海报路径，图片经本机转发（沿用 TMDB 代理）。
POSTER_SIZES = {"w185", "w342"}
POSTER_NAME = re.compile(r"^[A-Za-z0-9_-]{6,64}\.(?:jpg|jpeg|png|webp)$")
_poster_proxies: dict | None = None
_poster_cache: dict[str, tuple[bytes, str]] = {}
POSTER_CACHE_LIMIT = 96


def trending_posters(origin: str, token: str, proxies: dict | None, limit: int = 40) -> list[str]:
    global _poster_proxies
    _poster_proxies = proxies
    names: list[str] = []
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    for page in (1, 2):
        try:
            response = requests.get(origin + "/trending/all/week", params={"language": "zh-CN", "page": page},
                                    headers=headers, proxies=proxies, timeout=TIMEOUT)
            items = response.json().get("results") or [] if response.status_code == 200 else []
        except (requests.RequestException, ValueError, AttributeError):
            break
        for item in items:
            path = str(item.get("poster_path") or "").lstrip("/")
            if POSTER_NAME.match(path) and path not in names:
                names.append(path)
        if len(names) >= limit:
            break
    return names[:limit]


def poster(size: str, name: str) -> tuple[bytes, str] | None:
    """读取一张海报；只接受 TMDB 的尺寸与文件名格式，结果缓存在内存中。"""
    if size not in POSTER_SIZES or not POSTER_NAME.match(name):
        return None
    key = f"{size}/{name}"
    if key in _poster_cache:
        return _poster_cache[key]
    proxies = _poster_proxies
    if proxies is None and os.environ.get("TMDB_PROXY", "").strip():
        proxy = os.environ["TMDB_PROXY"].strip()
        proxies = {"http": proxy, "https": proxy}
    try:
        response = requests.get(f"{tmdb.image_origin()}/{key}", proxies=proxies, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    kind = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not kind.startswith("image/"):
        return None
    if len(_poster_cache) >= POSTER_CACHE_LIMIT:
        _poster_cache.pop(next(iter(_poster_cache)))
    _poster_cache[key] = (response.content, kind)
    return _poster_cache[key]


def test_llm(form: dict) -> dict:
    base = _field(form, "LLM_BASE_URL")
    key, model = _field(form, "LLM_API_KEY"), _field(form, "LLM_MODEL")
    if not base:
        raise SetupError("请选择平台或填写接口地址。")
    base = _url(base, "接口地址")
    if not key or not model:
        raise SetupError("请填写 API Key 和模型名称。")
    details, warnings = [], []
    try:
        response = requests.get(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"},
                                timeout=TIMEOUT)
    except requests.RequestException as error:
        return {"ok": False, "message": _network_message(error, "接口") + "请检查接口地址。"}
    if response.status_code in (401, 403):
        return {"ok": False, "message": "API Key 无效或没有权限。"}
    if response.status_code == 200:
        try:
            names = {str(item.get("id", "")).removeprefix("models/") for item in response.json().get("data") or []}
        except (ValueError, AttributeError):
            names = set()
        if names and model not in names:
            warnings.append(f"模型列表里没有“{model}”，请核对名称（区分大小写）。")
    # 真实发一次极小的 JSON 请求：能发现余额不足、模型名错误、不支持 JSON 模式等问题。
    try:
        result = llm.probe(base, key, model)
    except llm.LlmError as error:
        return {"ok": False, "message": str(error), "warnings": warnings}
    except requests.RequestException as error:
        return {"ok": False, "message": _network_message(error, "接口") + "请稍后重试。", "warnings": warnings}
    except ValueError as error:
        return {"ok": True, "message": "已连接", "details": ["接口可访问"],
                "warnings": [*warnings, f"测试请求未返回有效 JSON（{error}），识别时可能失败。"]}
    details.append(f"{'Responses API' if result['api'] == 'responses' else 'Chat Completions'} · "
                   f"{result['elapsed']:g} 秒")
    if result["provider"] == "deepseek":
        details.append("已关闭思考模式，按并发限流")
    return {"ok": True, "message": "已连接", "details": details, "warnings": warnings}


def test_mdblist(form: dict) -> dict:
    key = _field(form, "MDBLIST_APIKEY")
    if not key:
        raise SetupError("请填写 MDBList API Key。")
    try:
        response = requests.get("https://mdblist.com/api/", params={"apikey": key, "i": "tt0816692"}, timeout=TIMEOUT)
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        return {"ok": False, "message": _network_message(error, " MDBList") if isinstance(error, requests.RequestException)
                else "MDBList 返回了无法识别的内容。"}
    if response.status_code != 200 or payload.get("response") is False or payload.get("error"):
        return {"ok": False, "message": "API Key 无效。"}
    return {"ok": True, "message": "已连接", "details": ["每天可查询 1000 次"]}


def test_ffprobe(form: dict) -> dict:
    configured = _field(form, "FFPROBE_PATH")
    if configured:
        try:
            result = subprocess.run([configured, "-version"], capture_output=True, text=True, timeout=8,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "message": "无法运行这个 ffprobe，请检查路径。"}
        if result.returncode != 0:
            return {"ok": False, "message": "这个程序不是可用的 ffprobe。"}
        match = re.search(r"ffprobe version (\S+)", (result.stdout.splitlines() or [""])[0])
        return {"ok": True, "message": "可以使用", "details": [f"ffprobe {match.group(1)}" if match else configured]}
    status = ffprobe_status()
    if not status["ok"]:
        return {"ok": False, "message": "没有找到 ffprobe：请安装 FFmpeg 并加入 PATH，或填写程序路径。"}
    return {"ok": True, "message": "可以使用",
            "details": [f"ffprobe {status['version']}" if status["version"] else status["path"],
                        "随安装包提供" if status["bundled"] else status["path"]]}


def test_player(form: dict) -> dict:
    path = _field(form, "MPC_PATH") or r"C:\Program Files\MPC-HC\mpc-hc64.exe"
    if not Path(path).is_file():
        return {"ok": False, "message": "没有找到 MPC-HC，请填写程序路径。"}
    web = _url(_field(form, "MPC_WEB_URL") or "http://127.0.0.1:13579", "Web 接口")
    details, warnings = ["已找到 MPC-HC"], []
    try:
        requests.get(web + "/variables.html", timeout=2)
        details.append("Web 接口已开启")
    except requests.RequestException:
        warnings.append("Web 接口没有响应：MPC-HC 未运行或未开启“Web 界面”，播放时无法记录进度。")
    return {"ok": True, "message": "可以播放", "details": details, "warnings": warnings}


TESTS = {"alist": test_alist, "tmdb": test_tmdb, "llm": test_llm, "mdblist": test_mdblist,
         "ffprobe": test_ffprobe, "player": test_player}


def run_test(service: str, form: dict) -> dict:
    if service not in TESTS:
        raise SetupError("未知的测试项。")
    return TESTS[service](form if isinstance(form, dict) else {})


# ---------------------------------------------------------------- 保存


def save(form: dict, database: Path) -> dict:
    """写入 .env、立即生效，并在首次使用时建好空媒体库。"""
    if not isinstance(form, dict):
        raise SetupError("提交的数据格式不正确。")
    updates: dict[str, str | None] = {}
    origin = _field(form, "ALIST_ORIGIN") or "http://127.0.0.1:5244"
    updates["ALIST_ORIGIN"] = _url(origin, "AList 地址")
    for name in ("ALIST_USERNAME", "ALIST_PASSWORD", "TMDB_TOKEN"):
        value = _field(form, name)
        if not value:
            raise SetupError("AList 账号和 TMDB 令牌是必填项。")
        updates[name] = value
    updates["ALIST_PATH_PASSWORD"] = _field(form, "ALIST_PATH_PASSWORD") or None
    proxy = _field(form, "TMDB_PROXY")
    updates["TMDB_PROXY"] = _url(proxy, "代理地址") if proxy else None

    if form.get("ai_enabled"):
        base = _url(_field(form, "LLM_BASE_URL"), "AI 接口地址") if _field(form, "LLM_BASE_URL") else ""
        key, model = _field(form, "LLM_API_KEY"), _field(form, "LLM_MODEL")
        if not (base and key and model):
            raise SetupError("启用 AI 识别需要填写接口地址、API Key 和模型名称。")
        rpm = _field(form, "LLM_RPM")
        if llm.provider(base) == "deepseek":
            base = re.sub(r"/v1/?$", "", base.rstrip("/"))  # 官方地址不带 /v1
            rpm = ""  # DeepSeek 不限每分钟次数，只限并发
        elif rpm and (not rpm.isdigit() or not 1 <= int(rpm) <= 1000):
            raise SetupError("每分钟请求上限需要是 1–1000 的整数。")
        updates.update(LLM_BASE_URL=base, LLM_API_KEY=key, LLM_MODEL=model, LLM_RPM=rpm or None)
    else:
        updates.update(LLM_BASE_URL=None, LLM_API_KEY=None, LLM_MODEL=None, LLM_RPM=None)
    if form.get("mdblist_enabled"):
        key = _field(form, "MDBLIST_APIKEY")
        if not key:
            raise SetupError("启用 MDBList 评分需要填写 API Key。")
        updates["MDBLIST_APIKEY"] = key
    else:
        updates["MDBLIST_APIKEY"] = None
    if not os.environ.get("FLASK_SECRET_KEY", "").strip():
        updates["FLASK_SECRET_KEY"] = secrets.token_hex(32)
    ffprobe = ffprobe_status()
    if ffprobe["ok"] and ffprobe["bundled"]:
        updates["FFPROBE_PATH"] = ffprobe["path"]

    created = ensure_library(database)
    env_file.update(ENV_PATH, updates, template=EXAMPLE_PATH)
    for name, value in updates.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    alist.forget_token()
    throttle.reset()
    return {"ok": True, "library_created": created, "secret_key": updates.get("FLASK_SECRET_KEY")}
