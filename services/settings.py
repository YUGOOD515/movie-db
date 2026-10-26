"""设置页：可配置项清单、校验、保存到 .env 并立即生效。

清单是唯一来源：设置页模板按它渲染，保存时按它校验，测试按它核对 `.env.example`。
密钥只回报“是否已保存”，不会回显；留空表示保持不变，显式传 None 表示清除。
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from connectors import alist, imdb_ratings, llm, player, throttle, tmdb
from services import env_file, setup_wizard

KEEP = object()  # 密钥留空：保持已保存的值


class SettingsInvalid(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("；".join(errors.values()))
        self.errors = errors


@dataclass(frozen=True)
class Setting:
    name: str
    label: str
    kind: str = "text"  # text / url / secret / path / int / float / bool / select
    default: object = None
    hint: str = ""
    placeholder: str = ""
    advanced: bool = False
    required: bool = False
    restart: bool = False  # 只在重启后生效
    low: float | None = None
    high: float | None = None
    options: tuple[tuple[str, str], ...] = ()  # (值, 显示名)；值为空表示默认
    on: str = "1"
    off: str = "0"

    @property
    def secret(self) -> bool:
        return self.kind == "secret"

    def clean(self, raw):
        """返回要写入的字符串、None（删除该项，回到默认）或 KEEP。"""
        if raw is None:
            if self.required:
                raise ValueError(f"{self.label}不能清除。")
            return None
        if self.kind == "bool":
            if isinstance(raw, str):
                raw = raw.strip().casefold() not in ("", "0", "false", "no", "off")
            value = self.on if raw else self.off
            default = self.on if self.default else self.off
            return None if value == default else value
        if not isinstance(raw, (str, int, float)):
            raise ValueError(f"{self.label}的格式不正确。")
        value = str(raw).strip()
        if self.secret:
            return KEEP if not value else value
        if not value:
            if self.required and self.default is None:
                raise ValueError(f"请填写{self.label}。")
            return None
        if self.kind == "url":
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError(f"{self.label}需要以 http:// 或 https:// 开头。")
            return value.rstrip("/")
        if self.kind in ("int", "float"):
            try:
                number = int(value) if self.kind == "int" else float(value)
            except ValueError:
                raise ValueError(f"{self.label}需要是{'整数' if self.kind == 'int' else '数字'}。") from None
            if (self.low is not None and number < self.low) or (self.high is not None and number > self.high):
                raise ValueError(f"{self.label}需要在 {self.low:g}–{self.high:g} 之间。")
            return str(number) if self.kind == "int" else f"{number:g}"
        if self.kind == "select":
            if value not in {option for option, _ in self.options}:
                raise ValueError(f"{self.label}的取值不正确。")
            return value
        if "\n" in value:
            raise ValueError(f"{self.label}不能换行。")
        return value

    def display(self) -> object:
        """页面上显示的当前值（密钥除外）。"""
        current = os.environ.get(self.name, "").strip()
        if self.kind == "bool":
            if not current:
                return bool(self.default)
            return current.casefold() not in ("0", "false", "no", "off")
        return current


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    description: str
    settings: tuple[Setting, ...]
    test: str | None = None  # 对应 setup_wizard.TESTS 的检测项
    icon: str = ""           # Bootstrap Icons 名称（不含 bi- 前缀）

    @property
    def basic(self):
        return [item for item in self.settings if not item.advanced]

    @property
    def advanced(self):
        return [item for item in self.settings if item.advanced]


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    description: str
    groups: tuple[Group, ...] = field(default_factory=tuple)
    icon: str = ""


S = Setting
IS_WINDOWS, IS_MACOS = os.name == "nt", sys.platform == "darwin"
_NO_PROGRESS = "（不记录进度）"
if IS_WINDOWS:
    PLAYER_SETTINGS: tuple[Setting, ...] = (
        S("PLAYER", "播放器", "select", options=(
            ("", "自动选择"), ("mpc-hc", "MPC-HC"), ("mpv", "mpv"), ("vlc", "VLC" + _NO_PROGRESS),
            ("system", "系统默认程序" + _NO_PROGRESS))),
        S("MPC_PATH", "MPC-HC 程序", "path", placeholder="C:\\Program Files\\MPC-HC\\mpc-hc64.exe"),
        S("MPC_WEB_URL", "Web 接口", "url", placeholder="http://127.0.0.1:13579",
          hint="在 MPC-HC“选项 → Web 界面”中开启，用于记录观看进度。"),
        S("PLAYER_PATH", "其他播放器程序", "path", advanced=True, placeholder="C:\\Program Files\\mpv\\mpv.exe",
          hint="使用 mpv 或 VLC 时填写；留空自动查找。"),
    )
else:
    PLAYER_SETTINGS = (
        S("PLAYER", "播放器", "select", options=(
            ("", "自动选择"), *((("iina", "IINA"),) if IS_MACOS else ()), ("mpv", "mpv"),
            ("vlc", "VLC" + _NO_PROGRESS), ("system", "系统默认程序" + _NO_PROGRESS)),
          hint="IINA 和 mpv 可以记录观看进度。" if IS_MACOS else "mpv 可以记录观看进度。"),
        S("PLAYER_PATH", "程序路径", "path",
          placeholder="/Applications/IINA.app" if IS_MACOS else "/usr/bin/mpv",
          hint="留空自动查找" + ("；可以直接选择 .app。" if IS_MACOS else "。")),
    )
ALIST_BINARY = "alist.exe" if IS_WINDOWS else "alist"
SECTIONS: tuple[Section, ...] = (
    Section("connection", "连接", "影片来源、播放器与本机服务", (
        Group("alist", "AList", "从网盘读取和播放影片", (
            S("ALIST_ORIGIN", "地址", "url", "http://127.0.0.1:5244", placeholder="http://127.0.0.1:5244"),
            S("ALIST_USERNAME", "用户名", required=True),
            S("ALIST_PASSWORD", "密码", "secret", required=True),
            S("ALIST_PATH_PASSWORD", "路径密码", "secret", hint="目录在 AList“元信息”里设置了密码时填写。"),
            S("ALIST_AUTOSTART", "随 MovieDB 启动 AList", "bool", True, advanced=True,
              hint="需要同时填写 AList 程序目录。"),
            S("ALIST_DIR", "AList 程序目录", "path", advanced=True, hint=f"包含 {ALIST_BINARY} 的文件夹。",
              placeholder="D:\\Apps\\alist" if IS_WINDOWS else "/opt/homebrew/bin" if IS_MACOS else "/usr/local/bin"),
            S("ALIST_IMPORT_ROOT", "导入根目录", advanced=True, hint="只允许导入这个目录下的文件，留空不限制。",
              placeholder="/夸克/影视"),
            S("ALIST_RATE", "每秒请求数", "float", 20, advanced=True, low=0.1, high=100, placeholder="20",
              hint="网盘风控严格时调低。"),
            S("ALIST_CONCURRENCY", "同时请求数", "int", 4, advanced=True, low=1, high=16, placeholder="4"),
        ), test="alist", icon="hdd-network"),
        Group("scan", "待关联扫描", "发现新文件、检查失效文件", (
            S("MOVIEDB_AUTOSCAN", "启动时自动扫描", "bool", True,
              hint="MovieDB 启动后在后台扫描已选择的片库目录。"),
            S("IMPORT_TITLE_CONCURRENCY", "核心关联作品并行数", "int", 4, advanced=True, low=1, high=16, placeholder="4",
              hint="多个作品共用接口限额和视频探测池；图片与评分在独立资源池继续补齐。"),
        ), icon="radar"),
        Group("player", "播放器", "播放影片并记录观看进度", PLAYER_SETTINGS, test="player", icon="play-circle"),
        Group("ffprobe", "ffprobe", "读取分辨率、HDR 与音轨", (
            S("FFPROBE_PATH", "程序路径", "path", placeholder="ffprobe", hint="安装包已自带；留空使用系统 PATH 中的 ffprobe。"),
            S("FFPROBE_TIMEOUT", "超时（秒）", "int", 60, advanced=True, low=5, high=600, placeholder="60"),
            S("FFPROBE_CONCURRENCY", "并行进程数", "int", 3, advanced=True, low=1, high=8, placeholder="3"),
            S("FFPROBE_PER_SOURCE", "每个来源的并行数", "int", advanced=True, low=1, high=8, placeholder="跟随总并行数",
              hint="按 AList 路径的第一级目录区分来源，风控严格时调低。"),
            S("IMAGE_CONCURRENCY", "图片处理并行数", "int", 2, advanced=True, low=1, high=8, placeholder="2"),
        ), test="ffprobe", icon="film"),
        Group("sync", "云端只读快照", "本机离线时，在 Cloudflare 上浏览片库", (
            S("MOVIEDB_SYNC_TOKEN", "同步令牌", "secret", hint="设置后，媒体库变化时自动同步到云端。"),
            S("MOVIEDB_SYNC_DISABLED", "暂停自动同步", "bool", False),
            S("MOVIEDB_SYNC_URL", "同步地址", "url", advanced=True, placeholder="https://moviedb-router.example.workers.dev"),
        ), icon="cloud-arrow-up"),
        Group("app", "本机", "MovieDB 自身", (
            S("MOVIEDB_PORT", "端口", "int", 5000, low=1024, high=65535, restart=True, placeholder="5000",
              hint="桌面版使用的本机端口。"),
            S("MOVIEDB_UI", "界面打开方式", "select", restart=True,
              options=(("", "独立窗口（默认）"), ("browser", "系统浏览器")),
              hint="桌面版的界面显示在 MovieDB 自己的窗口里，还是用系统浏览器打开。"),
        ), icon="pc-display"),
    ), icon="plug"),
    Section("metadata", "元数据", "海报、简介、演员与评分", (
        Group("tmdb", "TMDB", "海报、简介、演员与季集", (
            S("TMDB_PROXY", "代理", "url", placeholder="http://127.0.0.1:7890", hint="只用于访问 TMDB。"),
            S("TMDB_TOKEN", "自己的密钥", "secret", advanced=True,
              hint="留空使用 MovieDB 内置的密钥。API 读访问令牌或 32 位 API Key 都可以。"),
            S("TMDB_API_ORIGIN", "接口地址", "url", advanced=True, placeholder="https://api.themoviedb.org/3",
              hint="留空时自动选择：api.themoviedb.org 连不上就改用 api.tmdb.org。使用反向代理或镜像时填写。"),
            S("TMDB_IMAGE_ORIGIN", "图片地址", "url", advanced=True, placeholder="https://image.tmdb.org/t/p",
              hint="海报加载不出来时，可填公共图片加速 https://wsrv.nl/?url=https://image.tmdb.org/t/p（第三方服务）。"),
            S("TMDB_RATE", "每秒请求预算", "float", 25, advanced=True, low=0.1, high=30, placeholder="25",
              hint="扫描、识别与导入共用预算；不积攒突发额度。"),
        ), test="tmdb", icon="image"),
        Group("mdblist", "MDBList", "烂番茄、Metacritic 等多平台评分", (
            S("MDBLIST_APIKEY", "API Key", "secret", hint="在 mdblist.com 登录后，于 Preferences → API Access 中生成；免费账号每天 1000 次。"),
            S("MDBLIST_DAILY_BUDGET", "24 小时请求预算", "int", 900, advanced=True, low=1, high=100000, placeholder="900",
              hint="按连续 24 小时统计，包含重试。根据账号套餐设置并保留余量。"),
            S("MDBLIST_RATE", "每秒请求预算", "float", 2, advanced=True, low=0.1, high=2, placeholder="2"),
            S("MOVIEDB_API_USAGE_DATABASE", "请求预算与评分缓存位置", "path", advanced=True,
              hint="留空使用数据目录的 api-usage.db；更换位置前先迁移原文件以保留请求计数。"),
        ), test="mdblist", icon="bar-chart"),
        Group("imdb", "IMDb 评分", "官方评分文件，超过一天自动重新下载", (), icon="star"),
        Group("douban", "豆瓣评分", "录入和刷新评分时一并获取", (
            S("DOUBAN_ENABLED", "获取豆瓣评分", "bool", True,
              hint="使用豆瓣 App 的非官方接口，可能随时失效；请求过快时豆瓣会暂时拒绝访问，MovieDB 会自动暂停 30 分钟。"),
        ), icon="chat-square-quote"),
    ), icon="collection"),
    Section("ai", "AI", "识别文件名拿不准的影片", (
        Group("llm", "AI 识别", "文件名规则拿不准时，让大模型判断片名、年份和季集", (
            S("LLM_BASE_URL", "接口地址", "url", placeholder="https://…/v1"),
            S("LLM_MODEL", "模型"),
            S("LLM_API_KEY", "API Key", "secret"),
            S("LLM_RPM", "每分钟请求上限", "int", 10, low=1, high=1000, placeholder="10",
              hint="免费额度通常按分钟限次。DeepSeek 只限并发，忽略此项。"),
            S("LLM_API", "接口类型", "select", advanced=True,
              options=(("", "自动"), ("responses", "Responses API"), ("chat", "Chat Completions"))),
            S("LLM_THINKING", "思考模式", "select", advanced=True, hint="仅 DeepSeek 与智谱生效，识别片名不需要思考。",
              options=(("", "关闭（默认）"), ("low", "低"), ("high", "高"), ("max", "最高"))),
            S("LLM_BATCH_SIZE", "每次请求条数", "int", advanced=True, low=1, high=50, placeholder="自动",
              hint="留空时 DeepSeek 为 8，其他平台为 20。"),
            S("LLM_CONCURRENCY", "同时请求数", "int", advanced=True, low=1, high=500, placeholder="自动",
              hint="留空时 DeepSeek 为 64，其他平台为 2；遇到限流会自动减半再逐步恢复。"),
        ), test="llm", icon="stars"),
    ), icon="stars"),
    Section("data", "数据", "清除本机数据，回到首次使用", icon="database"),
)
del S

FIELDS: dict[str, Setting] = {item.name: item for section in SECTIONS for group in section.groups for item in group.settings}
AI_CORE = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")

_pending_restart: set[str] = set()
_lock = threading.Lock()


# ---------------------------------------------------------------- 状态


def _ratings_status() -> dict:
    path = Path(imdb_ratings.RATINGS_PATH)
    if not path.is_file():
        return {"state": "off", "text": "尚未下载，首次录入时自动获取"}
    hours = (time.time() - path.stat().st_mtime) / 3600
    when = "刚刚" if hours < 1 else f"{int(hours)} 小时前" if hours < 48 else f"{int(hours // 24)} 天前"
    return {"state": "ok", "text": f"{when}更新"}


def _tmdb_status() -> dict:
    source = tmdb.token_source()
    if not source:
        return {"state": "warn", "text": "需要填写密钥"}
    return {"state": "ok", "text": "自己的密钥" if source == "user" else "内置密钥"}


def _player_status() -> dict:
    try:
        kind, _path = player.resolve_player()
    except player.PlayerNotFound:
        return {"state": "warn", "text": "未找到播放器"}
    if kind == "system":
        return {"state": "off", "text": "使用系统默认程序"}
    return {"state": "ok", "text": f"已找到 {player.label(kind)}"}


def statuses() -> dict:
    env = os.environ.get
    ffprobe = setup_wizard.ffprobe_status()
    configured = lambda ok, on="已配置", off="未配置": {"state": "ok" if ok else "off", "text": on if ok else off}  # noqa: E731
    from services import cloud_sync

    sync_token = bool(env("MOVIEDB_SYNC_TOKEN", "").strip())
    return {
        "alist": configured(env("ALIST_USERNAME", "").strip() and env("ALIST_PASSWORD", "")),
        "scan": configured(FIELDS["MOVIEDB_AUTOSCAN"].display(), "已开启", "已关闭"),
        "player": _player_status(),
        "ffprobe": {"state": "ok", "text": f"已就绪{' · ' + ffprobe['version'] if ffprobe['version'] else ''}"}
        if ffprobe["ok"] else {"state": "warn", "text": "未找到"},
        "sync": {"state": "ok", "text": "自动同步中"} if cloud_sync.is_enabled()
        else {"state": "off", "text": "已暂停" if sync_token else "未开启"},
        "app": {"state": "warn", "text": "等待重启"} if _pending_restart else {"state": "ok", "text": "运行中"},
        "tmdb": _tmdb_status(),
        "mdblist": configured(env("MDBLIST_APIKEY", "").strip(), off="未开启"),
        "imdb": _ratings_status(),
        "douban": configured(FIELDS["DOUBAN_ENABLED"].display(), "已开启", "已关闭"),
        "llm": configured(llm.is_configured(), "已开启", "未开启"),
    }


def state(*, restart_supported: bool) -> dict:
    return {
        "values": {name: item.display() for name, item in FIELDS.items() if not item.secret},
        "saved": {name: bool(os.environ.get(name, "").strip()) for name, item in FIELDS.items() if item.secret},
        "status": statuses(),
        "presets": setup_wizard.LLM_PRESETS,
        "ai_enabled": llm.is_configured(),
        "pending_restart": sorted(_pending_restart),
        "restart_supported": restart_supported,
    }


# ---------------------------------------------------------------- 保存


def _effective(name: str, updates: dict) -> str:
    if name in updates:
        return updates[name] or ""
    return os.environ.get(name, "").strip()


def clean(values: dict, ai_enabled: bool | None = None) -> dict[str, str | None]:
    if not isinstance(values, dict):
        raise SettingsInvalid({"_": "提交的数据格式不正确。"})
    updates: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    for name, raw in values.items():
        item = FIELDS.get(name)
        if item is None:
            errors[name] = f"未知的设置项：{name}"
            continue
        try:
            value = item.clean(raw)
        except ValueError as error:
            errors[name] = str(error)
            continue
        if value is not KEEP:
            updates[name] = value

    if ai_enabled is False:
        updates.update(dict.fromkeys((*AI_CORE, "LLM_RPM"), None))
    elif ai_enabled or any(name in updates for name in AI_CORE):
        base = _effective("LLM_BASE_URL", updates)
        for name, label in (("LLM_BASE_URL", "接口地址"), ("LLM_API_KEY", "API Key"), ("LLM_MODEL", "模型")):
            if not _effective(name, updates) and name not in errors:
                errors[name] = f"开启 AI 识别需要填写{label}。"
        if base and llm.provider(base) == "deepseek":
            updates["LLM_BASE_URL"] = re.sub(r"/v1/?$", "", base.rstrip("/"))  # 官方地址不带 /v1
            if os.environ.get("LLM_RPM") or updates.get("LLM_RPM"):
                updates["LLM_RPM"] = None  # DeepSeek 只限并发，不限每分钟次数

    for name, item in FIELDS.items():
        if item.required and item.kind != "bool" and item.default is None and name not in errors:
            if name in updates and not updates[name]:
                errors[name] = f"请填写{item.label}。"
    if errors:
        raise SettingsInvalid(errors)
    return updates


def save(values: dict, ai_enabled: bool | None = None) -> dict:
    """写入 .env 并更新当前进程的环境变量；返回实际变化的项与需要重启的项。"""
    updates = clean(values, ai_enabled)
    with _lock:
        changed = sorted(name for name, value in updates.items() if (os.environ.get(name) or None) != value)
        if changed:
            env_file.update(setup_wizard.ENV_PATH, {name: updates[name] for name in changed},
                            template=setup_wizard.EXAMPLE_PATH)
            for name in changed:
                if updates[name] is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = updates[name]
        restart = [name for name in changed if FIELDS.get(name) and FIELDS[name].restart]
        _pending_restart.update(restart)
    return {"ok": True, "changed": changed, "restart": sorted(_pending_restart)}


def apply(changed: list[str], app) -> list[str]:
    """让已保存的修改立即生效；返回执行过的动作说明（用于日志与测试）。"""
    changed = set(changed)
    actions = []
    if changed & {"ALIST_ORIGIN", "ALIST_USERNAME", "ALIST_PASSWORD", "ALIST_PATH_PASSWORD"}:
        alist.forget_token()
        app.config["ALIST_ORIGIN"] = os.environ.get("ALIST_ORIGIN", "http://127.0.0.1:5244").rstrip("/")
        actions.append("alist-login")
    if changed:
        throttle.configure()  # 保留正在进行的请求和服务冷却时间
        actions.append("throttle")
    testing = app.config.get("TESTING")
    if changed & {"ALIST_DIR", "ALIST_AUTOSTART", "ALIST_ORIGIN"} and not testing:
        from services.alist_process import start_configured_alist

        threading.Thread(target=start_configured_alist, name="alist-start", daemon=True).start()
        actions.append("alist-start")
    if changed & {"MOVIEDB_AUTOSCAN", "ALIST_USERNAME", "ALIST_PASSWORD"} and not testing \
            and not os.environ.get("MOVIEDB_AUTOSCAN_DISABLED"):
        from services.media_discovery import start_autoscan

        start_autoscan(app.config["DATABASE"])
        actions.append("autoscan")
    if changed & {"MOVIEDB_SYNC_TOKEN", "MOVIEDB_SYNC_DISABLED"}:
        from services import cloud_sync

        cloud_sync.init_app(app)  # 已启动时只是继续运行；暂停与恢复由监视线程按当前设置判断
        actions.append("sync")
    return actions


def clear_pending_restart() -> None:
    with _lock:
        _pending_restart.clear()
