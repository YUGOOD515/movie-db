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
from urllib.parse import urlparse

from connectors import llm, player, throttle, tmdb
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
    icon: str = ""           # Lucide 图标名称

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
    hue: str = "gray"  # 分区色，只用于分组图标块（style.css 的 --hue-*）


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
SECTIONS: tuple[Section, ...] = (
    Section("connection", "连接", "播放器与本机服务", (
        Group("player", "播放器", "播放影片并记录观看进度", PLAYER_SETTINGS, test="player", icon="circle-play"),
        Group("ffprobe", "ffprobe", "读取分辨率、HDR 与音轨", (
            S("FFPROBE_PATH", "程序路径", "path", placeholder="ffprobe", hint="安装包已自带；留空使用系统 PATH 中的 ffprobe。"),
            S("FFPROBE_TIMEOUT", "超时（秒）", "int", 60, advanced=True, low=5, high=600, placeholder="60"),
            S("FFPROBE_CONCURRENCY", "并行进程数", "int", 3, advanced=True, low=1, high=8, placeholder="3"),
            S("FFPROBE_PER_SOURCE", "每个来源的并行数", "int", advanced=True, low=1, high=8, placeholder="跟随总并行数",
              hint="按挂载路径的第一级目录区分来源，风控严格时调低。"),
            S("IMAGE_CONCURRENCY", "图片处理并行数", "int", 2, advanced=True, low=1, high=8, placeholder="2"),
        ), test="ffprobe", icon="film"),
        Group("sync", "云端只读快照", "本机离线时，在 Cloudflare 上浏览片库", (
            S("MOVIEDB_SYNC_TOKEN", "同步令牌", "secret", hint="设置后，媒体库变化时自动同步到云端。"),
            S("MOVIEDB_SYNC_DISABLED", "暂停自动同步", "bool", False),
            S("MOVIEDB_SYNC_URL", "同步地址", "url", advanced=True, placeholder="https://moviedb-router.example.workers.dev"),
        ), icon="cloud-upload"),
        Group("app", "本机", "MovieDB 自身", (
            S("MOVIEDB_PORT", "端口", "int", 5000, low=1024, high=65535, restart=True, placeholder="5000",
              hint="桌面版使用的本机端口。"),
            S("MOVIEDB_UI", "界面打开方式", "select", restart=True,
              options=(("", "独立窗口（默认）"), ("browser", "系统浏览器")),
              hint="桌面版的界面显示在 MovieDB 自己的窗口里，还是用系统浏览器打开。"),
            S("MOVIEDB_ALLOWED_HOSTS", "允许访问的主机名", advanced=True, placeholder="library.example.com",
              hint="通过 Cloudflare Tunnel 等域名访问时填写，多个用逗号分隔；填写后其他主机名的请求一律拒绝。本机地址始终可用。"),
        ), icon="monitor"),
    ), icon="plug", hue="blue"),
    Section("metadata", "元数据", "海报、简介、演员与评分", (
        Group("tmdb", "TMDB", "海报、简介、演员与季集", (
            S("TMDB_TOKEN", "自己的密钥", "secret",
              hint="留空使用 MovieDB 内置的密钥。API 读访问令牌或 32 位 API Key 都可以。"),
            S("TMDB_API_ORIGIN", "接口地址", "url", placeholder="https://api.themoviedb.org/3",
              hint="留空时自动选择：api.themoviedb.org 连不上就改用 api.tmdb.org。使用反向代理或镜像时填写。"),
            S("TMDB_IMAGE_ORIGIN", "图片地址", "url", placeholder="https://image.tmdb.org/t/p",
              hint="海报加载不出来时，可填公共图片加速 https://wsrv.nl/?url=https://image.tmdb.org/t/p（第三方服务）。"),
            S("TMDB_RATE", "每秒请求预算", "float", 25, low=0.1, high=30, placeholder="25",
              hint="扫描、识别与导入共用预算；不积攒突发额度。"),
        ), test="tmdb", icon="image"),
        Group("mdblist", "MDBList", "烂番茄、Metacritic 等多平台评分", (
            S("MDBLIST_APIKEY", "API Key", "secret", hint="在 mdblist.com 登录后，于 Preferences → API Access 中生成；免费账号每天 1000 次。"),
            S("MDBLIST_DAILY_BUDGET", "24 小时请求预算", "int", 900, advanced=True, low=1, high=100000, placeholder="900",
              hint="按连续 24 小时统计，包含重试。根据账号套餐设置并保留余量。"),
            S("MDBLIST_RATE", "每秒请求预算", "float", 2, advanced=True, low=0.1, high=2, placeholder="2"),
        ), test="mdblist", icon="chart-column"),
        Group("imdb", "IMDb 数据", "官方评分与单集编号，一起下载、一起更新", (
            S("IMDB_EPISODES_UPDATE", "更新方式", "select", options=(
                ("", "每周（默认）"), ("daily", "每天"), ("manual", "仅手动")),
              hint="MovieDB 运行时检查；停机期间错过的检查，下次启动后补上。更新时仍可使用旧版本。"),
        ), icon="star"),
    ), icon="library", hue="amber"),
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
        ), test="llm", icon="sparkles"),
    ), icon="sparkles", hue="violet"),
)
del S

FIELDS: dict[str, Setting] = {item.name: item for section in SECTIONS for group in section.groups for item in group.settings}
AI_CORE = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")

_pending_restart: set[str] = set()
_lock = threading.Lock()


# ---------------------------------------------------------------- 状态


# 正常时统一写“正常”，需要补充的信息放在“·”后面；未配置 / 已关闭为灰色，异常写具体原因
OK = "正常"


def _ok(detail: str = "") -> dict:
    return {"state": "ok", "text": f"{OK} · {detail}" if detail else OK}


def _tmdb_status() -> dict:
    source = tmdb.token_source()
    if not source:
        return {"state": "warn", "text": "未配置"}
    return _ok("自己的密钥" if source == "user" else "内置密钥")


def _player_status() -> dict:
    try:
        kind, _path = player.resolve_player()
    except player.PlayerNotFound:
        return {"state": "warn", "text": "未找到"}
    return _ok("系统默认程序" if kind == "system" else player.label(kind))


def _short_version(version: str) -> str:
    """状态标签只放简短版本号：7.1.1-essentials_build-… → 7.1.1，2026-02-04-git-627da11…-full_build → 2026-02-04。"""
    match = re.match(r"n?(\d+(?:\.\d+)+)|(\d{4}-\d{2}-\d{2})", version or "")
    return (match.group(1) or match.group(2)) if match else (version or "")[:16]


def imdb_status(index: dict) -> dict:
    """IMDb 数据的状态标签：更新中显示进度，正常时显示数据更新日期。"""
    if index.get("running"):
        if index.get("phase") == "build":
            return {"state": "ok", "text": "更新中 · 建立索引"}
        return {"state": "ok", "text": f"更新中 · 已下载 {index.get('downloaded', 0) / 1048576:.0f} MiB"}
    if index.get("error"):
        return {"state": "warn", "text": "更新失败"}
    if not index.get("available"):
        return {"state": "off", "text": "未建立"}
    updated = time.localtime(index.get("updated_at") or 0)
    same_year = updated.tm_year == time.localtime().tm_year
    return _ok(time.strftime("%m-%d 更新" if same_year else "%Y-%m-%d 更新", updated))


def statuses() -> dict:
    env = os.environ.get
    ffprobe = setup_wizard.ffprobe_status()
    from services import cloud_sync
    from services import imdb_identity

    sync_token = bool(env("MOVIEDB_SYNC_TOKEN", "").strip())
    return {
        "player": _player_status(),
        "ffprobe": _ok(_short_version(ffprobe["version"])) if ffprobe["ok"] else {"state": "warn", "text": "未找到"},
        "sync": _ok() if cloud_sync.is_enabled() else {"state": "off", "text": "已关闭" if sync_token else "未配置"},
        "app": {"state": "warn", "text": "等待重启"} if _pending_restart else _ok(),
        "tmdb": _tmdb_status(),
        "mdblist": _ok() if env("MDBLIST_APIKEY", "").strip() else {"state": "off", "text": "未配置"},
        "imdb": imdb_status(imdb_identity.status()),
        "llm": _ok() if llm.is_configured() else {"state": "off", "text": "已关闭"},
    }


def state(*, restart_supported: bool) -> dict:
    from services import imdb_identity
    return {
        "values": {name: item.display() for name, item in FIELDS.items() if not item.secret},
        "saved": {name: bool(os.environ.get(name, "").strip()) for name, item in FIELDS.items() if item.secret},
        "status": statuses(),
        "presets": setup_wizard.LLM_PRESETS,
        "ai_enabled": llm.is_configured(),
        "pending_restart": sorted(_pending_restart),
        "restart_supported": restart_supported,
        "imdb": imdb_identity.status(),
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
    if changed:
        throttle.configure()  # 保留正在进行的请求和服务冷却时间
        actions.append("throttle")
    testing = app.config.get("TESTING")
    if "IMDB_EPISODES_UPDATE" in changed and not testing and not app.config.get("READ_ONLY"):
        from services import imdb_identity
        imdb_identity.wake_scheduler(app.config["DATABASE"])
        actions.append("imdb-episodes")
    if changed & {"MOVIEDB_SYNC_TOKEN", "MOVIEDB_SYNC_DISABLED"}:
        from services import cloud_sync

        cloud_sync.init_app(app)  # 已启动时只是继续运行；暂停与恢复由监视线程按当前设置判断
        actions.append("sync")
    return actions


def clear_pending_restart() -> None:
    with _lock:
        _pending_restart.clear()
