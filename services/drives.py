from __future__ import annotations

import base64
import functools
import json
import secrets
import threading
import time
from datetime import datetime

import requests

from connectors.drives import DRIVERS
from connectors.drives.base import DriveError
from models import drive_store
from services import drive_keepalive, media_source


def types():
    # 透传只能有一个：已存在时不再出现在“新建”的类型下拉里，但编辑它仍需要这份字段表单。
    has_passthrough = drive_store.get_drive("*") is not None
    return [{"kind": kind, "label": driver.label, "fields": driver.fields,
             "creatable": kind != "alist" or not has_passthrough,
             "qr_app": getattr(driver, "qr_app", "") if hasattr(driver, "start_login") else "",
             "oauth": oauth_url(driver), "oauth_page": oauth_page(driver),
             "oauth_embedded": bool(getattr(driver, "oauth_embedded", False))}
            for kind, driver in DRIVERS.items()]


def _oplist_base(driver):
    renew = next((field.default for field in driver.fields if field.name == "renew_api"), "")
    return renew.rsplit("/", 2)[0] if renew else "https://api.oplist.org"


def oauth_url(driver):
    """OpenList 的授权入口：返回 JSON {"text": 授权地址}，并在打开它的窗口里设置回调要用的 Cookie。

    桌面版的授权窗口先打开它、再跳到授权地址；驱动没有 oauth_txt 时为空。
    """
    driver_txt = getattr(driver, "oauth_txt", "")
    if not driver_txt:
        return ""
    return f"{_oplist_base(driver)}/{driver_txt.split('_')[0]}/requests?driver_txt={driver_txt}&server_use=true"


def oauth_page(driver):
    """OpenList 令牌页，# 后的回调数据预先选好网盘类型并勾选“使用官方参数”；浏览器里在这里完成授权。"""
    driver_txt = getattr(driver, "oauth_txt", "")
    if not driver_txt:
        return ""
    preset = base64.b64encode(json.dumps({"driver_txt": driver_txt, "server_use": "true"}).encode()).decode()
    return f"{_oplist_base(driver)}/#{preset}"


def _secret_fields(kind):
    return tuple(field.name for field in DRIVERS[kind].fields if field.kind == "secret")


def public(row, *, passthrough_names=frozenset(), passthrough=None):
    """passthrough_names：透传根目录下的目录名，用来提示“透传里也有同名目录”。"""
    fields = DRIVERS[row["kind"]].fields
    result = {**row, "config": {
        field.name: row["config"].get(field.name, field.default)
        for field in fields if field.kind != "secret"
    }}
    result["label"] = DRIVERS[row["kind"]].label
    result["category"] = result["label"].split("（", 1)[0]
    result["saved"] = {name: bool(row["config"].get(name)) for name in _secret_fields(row["kind"])}
    if not row["enabled"]:
        result["status_state"], result["status_text"] = "off", "已停用"
    elif not row["status"]:
        result["status_state"], result["status_text"] = "warn", "尚未检测"
    else:
        connected = row["status"].startswith("已连接")
        result["status_state"] = "ok" if connected else "fail"
        result["status_text"] = "正常" if connected else "检测失败"
    # 网盘页展示：透传挂载 "*" 没有可读的名字，按用途命名；详情只在比状态标签多出信息时显示。
    is_passthrough = row["mount"] == "*"
    result["display_name"] = media_source.PASSTHROUGH_NAME if is_passthrough else row["mount"]
    result["source_path"] = "*" if is_passthrough else f"/{row['mount']}"
    result["icon"] = "folder" if row["kind"] == "local" else "network"
    # 来源列表用品牌 logo：quark_tv、pan123_web 等登录方式与品牌同名前缀，缺 logo 时退回上面的通用图标
    brand = row["kind"].split("_", 1)[0]
    result["logo"] = f"icons/drives/{brand}.svg" if brand in logos() else ""
    result.update(_route_view(row, passthrough_names, passthrough))
    if result["route_state"] == "delegated":
        # 路径已交给透传：标签说明“谁在提供”，比本网盘的连接状态更重要
        result["status_state"], result["status_text"] = "off", "已移交透传"
    status = (row["status"] or "").strip()
    # 停用后旧的检测结果已不代表当前状态，不再显示
    result["status_detail"] = status if row["enabled"] and status and status != result["status_text"] else ""
    result["keepalive_text"] = _keepalive_text(row) if row["enabled"] else ""
    # 仅复用已有账号/检测结果；本机路径和服务地址不能当成账号，也不解析 Cookie 或令牌。
    account = str(result["config"].get("username") or "").strip()
    if status.startswith("已连接：") and row["kind"] not in ("local", "webdav", "alist"):
        account = status.removeprefix("已连接：").split("（会员类型", 1)[0].strip() or account
    result["account"] = account
    result["has_problem"] = result["status_state"] == "fail" or (row["enabled"] and result["route_state"] == "unavailable")
    # 详情标题下一行：状态后面接类型、账号、挂载路径和续期，没有的项不写；需要处理的情况逐条写在下面
    path = result["scope"] if is_passthrough else result["source_path"]
    result["summary"] = " · ".join(filter(None, (result["status_text"], result["label"], account, path, result["keepalive_text"])))
    notes = [result["status_detail"]] if result["status_state"] == "fail" and result["status_detail"] else []
    if row["enabled"] and result["route_state"] == "unavailable" and result.get("route_reason"):
        notes.append(result["route_reason"])
    if result["overlap"] and result["route_state"] == "self":
        notes.append(f"{media_source.PASSTHROUGH_NAME}里也有同名目录。")
    result["status_notes"] = notes
    return result


def _keepalive_text(row):
    kept = drive_keepalive.state(row["mount"])
    if not kept.get("ok"):
        return ""
    moment = datetime.fromisoformat(kept["at"]).astimezone()
    return f"{moment:%H:%M} 已续期" if row["kind"] != "alist" else f"{moment:%H:%M} 已检测"


def _route_view(row, passthrough_names, passthrough):
    """网盘页上的路由说明：这个前缀现在由谁提供、透传里是否也有同名目录、能否移交透传。"""
    if row["mount"] == "*":
        delegated = sorted(item["mount"] for item in drive_store.list_drives()
                           if item.get("delegated") and media_source.route(item["mount"])[0] == "*")
        scope = "未匹配挂载的路径" + (f"，以及 {'、'.join('/' + mount for mount in delegated)}" if delegated else "")
        return {"scope": scope, "provider": "*", "route_state": "self", "can_delegate": False, "overlap": False}
    provider, reason = media_source.route(row["mount"])
    overlap = row["mount"] in passthrough_names
    if provider == "*":
        state, text = "delegated", f"由{media_source.PASSTHROUGH_NAME}提供"
    elif provider is None:
        state, text = "unavailable", "已停用，文件暂不可用" if not row["enabled"] else reason
    else:
        state, text = "self", "由本网盘提供"
    if overlap and state == "self":
        text += f"（{media_source.PASSTHROUGH_NAME}里也有同名目录）"
    return {"scope": f"/{row['mount']} · {text}", "provider": provider, "route_state": state, "route_reason": reason,
            "overlap": overlap, "can_delegate": bool(passthrough) and (overlap or row.get("delegated"))}


def _passthrough_names(passthrough):
    """透传根目录下的目录名（目录缓存 30 分钟）；读不到时不提示重叠。"""
    if not passthrough or not passthrough["enabled"]:
        return frozenset()
    try:
        drive = media_source.drive_for("*")
        return frozenset(entry.name for entry in media_source._list(drive, "*", drive.root()) if entry.is_dir)
    except Exception:
        return frozenset()


def listing():
    rows = drive_store.list_drives()
    passthrough = next((row for row in rows if row["mount"] == "*"), None)
    names = _passthrough_names(passthrough)
    return [public(row, passthrough_names=names, passthrough=passthrough) for row in rows]


def routes():
    """每个已配置挂载名 → 当前提供者（挂载名、"*" 或 None）；"*" 本身记为透传是否可用。"""
    result = {row["mount"]: media_source.route(row["mount"])[0] for row in drive_store.list_drives() if row["mount"] != "*"}
    passthrough = drive_store.get_drive("*")
    result["*"] = "*" if passthrough and passthrough["enabled"] else None
    return result


def rerouted(before):
    """对比操作前后的路由，返回换了来源的前缀（"*" 表示透传覆盖的其余路径）。"""
    after = routes()
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def route_message(changed):
    if not changed:
        return ""
    names = "、".join("其余路径" if key == "*" else f"/{key}" for key in changed)
    return f"{names} 的来源已变化，已开始重新扫描。"


def missing_mounts(paths):
    mounts = {path.strip("/").split("/", 1)[0] for path in paths if path and path != "/"}
    configured = {row["mount"] for row in drive_store.list_drives() if row["enabled"]}
    return sorted(mounts - configured) if "*" not in configured else []


def describe_error(error):
    if isinstance(error, DriveError):
        return str(error)
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, requests.RequestException):
        return "网盘暂时无法访问，请稍后重试。"
    return "网盘检测失败，请检查配置。"


def _driver(kind, config, save):
    driver = DRIVERS.get(kind)
    if driver is None:
        raise ValueError("不支持的网盘类型。")
    return driver(config, save)


def save(mount, kind, values, enabled=None, create=False, logged_in=None):
    """检测后保存挂载。logged_in 是扫码 / 授权刚登录好的驱动：直接用它检测，
    不必马上拿刚拿到的 refresh_token 再换一次令牌（换过之后旧令牌作废，同时有别的请求就会判成登录失效）。"""
    if kind not in DRIVERS:
        raise ValueError("不支持的网盘类型。")
    mount = "*" if kind == "alist" else str(mount or "").strip()
    if not mount or "/" in mount or mount in (".", "..") or (mount == "*") != (kind == "alist"):
        raise ValueError("请填写有效的挂载名，且不要包含 /。")
    previous = drive_store.get_drive(mount) or {}
    if create and previous:
        raise ValueError("挂载名已存在，请换一个或编辑原挂载")
    if previous and previous["kind"] != kind:
        raise ValueError("编辑时不能更改网盘类型。")
    if enabled is None:
        enabled = previous.get("enabled", True)
    config = dict(previous.get("config") or {})
    changed = False
    for field in DRIVERS[kind].fields:
        previous_value = config.get(field.name, field.default)
        value = values.get(field.name)
        if value is None:
            value = previous_value
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if field.kind == "secret" and not value and previous:
            continue
        config[field.name] = value or field.default
        if field.required and not config[field.name]:
            if field.manual:
                raise ValueError(f"请先完成扫码或授权登录，或在“手动填写凭据”里填写 {field.label}。")
            raise ValueError(f"请填写{field.label}。")
        changed = changed or config[field.name] != previous_value
    if changed:
        config = {field.name: config[field.name] for field in DRIVERS[kind].fields if field.name in config}
    candidate = dict(config)
    if logged_in is not None:
        driver = logged_in
        driver.save = lambda updated: candidate.update(updated)
    else:
        driver = _driver(kind, candidate, lambda updated: candidate.update(updated))
    try:
        status = driver.check()
    except Exception as error:
        raise DriveError(describe_error(error)) from None
    before = routes()
    drive_store.save_drive(mount, kind, candidate, enabled)
    drive_store.set_status(mount, status)
    media_source.forget()
    drive_keepalive.forget(mount)
    changed = rerouted(before)
    return {"ok": True, "status": status, "rerouted": changed, "message": route_message(changed)}


def test(mount):
    row = drive_store.get_drive(mount)
    if not row:
        return {"ok": False, "message": "找不到这个挂载。"}
    try:
        status = _driver(row["kind"], row["config"], lambda config: drive_store.update_config(mount, config)).check()
    except Exception as error:
        message = describe_error(error)
        drive_store.set_status(mount, message)
        media_source.forget(mount)
        return {"ok": False, "message": message}
    drive_store.set_status(mount, status)
    media_source.forget(mount)
    return {"ok": True, "status": status}


def toggle(mount):
    row = drive_store.get_drive(mount)
    if not row:
        return {"ok": False, "message": "找不到这个挂载。"}
    before = routes()
    drive_store.save_drive(mount, row["kind"], row["config"], not row["enabled"])
    media_source.forget()
    drive_keepalive.forget(mount)
    changed = rerouted(before)
    return {"ok": True, "enabled": not row["enabled"], "rerouted": changed, "message": route_message(changed)}


def delegate(mount, delegated):
    """同名挂载改由透传提供（True）或改回由本网盘提供（False）。"""
    row = drive_store.get_drive(mount)
    if not row or mount == "*":
        return {"ok": False, "message": "找不到这个挂载。"}
    if delegated and not drive_store.get_drive("*"):
        return {"ok": False, "message": f"还没有添加{media_source.PASSTHROUGH_NAME}。"}
    before = routes()
    drive_store.set_delegated(mount, delegated)
    if not delegated and not row["enabled"]:
        # “改回由本网盘提供”意味着要用它：一并启用，否则路径会直接变成不可用
        drive_store.save_drive(mount, row["kind"], row["config"], True)
    media_source.forget()
    drive_keepalive.forget(mount)
    changed = rerouted(before)
    return {"ok": True, "delegated": delegated, "rerouted": changed, "message": route_message(changed)}


def delete(mount):
    before = routes()
    drive_store.delete_drive(mount)
    media_source.forget()
    drive_keepalive.forget(mount)
    changed = rerouted(before)
    return {"ok": True, "rerouted": changed, "message": route_message(changed)}


# ---- 扫码登录（夸克 / UC 电视版）：会话只在服务端，页面拿到二维码与随机编号，令牌不经过浏览器 ----

QR_TTL = 300
_qr_lock = threading.Lock()
_qr_logins: dict[str, dict] = {}


def _mount_for(kind, mount, create):
    mount = str(mount or "").strip()
    if not mount or "/" in mount or mount in (".", "..", "*"):
        raise ValueError("请填写有效的挂载名，且不要包含 /。")
    previous = drive_store.get_drive(mount)
    if create and previous:
        raise ValueError("挂载名已存在，请换一个或编辑原挂载")
    if previous and previous["kind"] != kind:
        raise ValueError("编辑时不能更改网盘类型。")
    return mount, previous


def qr_start(kind, mount, create=False):
    driver_class = DRIVERS.get(kind)
    if driver_class is None or not hasattr(driver_class, "start_login"):
        raise ValueError("这种网盘不支持扫码登录。")
    mount, previous = _mount_for(kind, mount, create)
    # 编辑时沿用原设备 ID：令牌与设备绑定
    config = dict((previous or {}).get("config") or {})
    driver = driver_class(config, lambda updated: config.update(updated))
    try:
        login = driver.start_login()
    except Exception as error:
        raise DriveError(describe_error(error)) from None
    if not isinstance(login.get("state"), dict):
        raise DriveError("无法获取登录二维码，请稍后重试")
    login_id = secrets.token_urlsafe(16)
    now = time.monotonic()
    with _qr_lock:
        for key in [key for key, item in _qr_logins.items() if now - item["at"] > QR_TTL]:
            _qr_logins.pop(key, None)
        _qr_logins[login_id] = {"kind": kind, "mount": mount, "create": create, "driver": driver,
                                "config": config, "state": login["state"], "at": now}
    return {"ok": True, "login_id": login_id, "qr": _qr_source(login["qr"]), "hint": login.get("hint", ""),
            "expires_in": QR_TTL}


def _qr_source(value):
    """二维码可以是图片地址、data URI 或裸 base64（夸克返回 PNG）。"""
    if value.startswith(("data:", "https://", "http://")):
        return value
    return f"data:{'image/png' if value.startswith('iVBOR') else 'image/jpeg'};base64,{value}"


def qr_poll(login_id):
    with _qr_lock:
        item = _qr_logins.get(str(login_id or ""))
        if item and time.monotonic() - item["at"] > QR_TTL:
            _qr_logins.pop(login_id, None)
            item = None
    if item is None:
        return {"ok": False, "expired": True, "message": "二维码已过期，请重新获取。"}
    # 有的网盘（百度）规定了最短查询间隔：没到时间就不去问，直接回复“等待中”
    now = time.monotonic()
    if now - item.get("polled", 0) < float(item["state"].get("interval") or 0):
        return {"ok": True, "pending": True}
    item["polled"] = now
    try:
        done = item["driver"].finish_login(item["state"])
    except Exception as error:
        with _qr_lock:
            _qr_logins.pop(login_id, None)
        return {"ok": False, "message": describe_error(error)}
    if not done:
        return {"ok": True, "pending": True}
    with _qr_lock:
        _qr_logins.pop(login_id, None)
    config = item["config"]
    # 登录得到的凭据连同驱动生成的设备 ID、应用凭据一起保存：续期时要用同一组
    values = {field.name: config[field.name] for field in DRIVERS[item["kind"]].fields if config.get(field.name)}
    return save(item["mount"], item["kind"], values, enabled=True, create=item["create"], logged_in=item["driver"])


# ---- 网页授权（OneDrive、Google Drive）：OpenList 授权页完成后跳到 https://api.oplist.org/#<base64 JSON> ----

def _oauth_result(text):
    """授权结果：OpenList 回调网址（或其 # 部分）里的 base64 JSON，或直接粘贴的 refresh_token。"""
    fragment = text.split("#", 1)[1] if "#" in text else text
    try:
        data = json.loads(base64.b64decode(fragment + "=" * (-len(fragment) % 4)).decode("utf-8"))
        if isinstance(data, dict):
            return data
    except (ValueError, UnicodeDecodeError):
        pass
    if len(text) >= 20 and "://" not in text and not any(char.isspace() for char in text):
        return {"refresh_token": text}
    raise ValueError("无法识别授权结果，请粘贴授权页上显示的 Refresh Token。")


def oauth_finish(kind, mount, pasted, create=False):
    driver_class = DRIVERS.get(kind)
    if driver_class is None or not getattr(driver_class, "oauth_txt", ""):
        raise ValueError("这种网盘不支持网页授权。")
    mount, _ = _mount_for(kind, mount, create)
    data = _oauth_result(str(pasted or "").strip())
    if data.get("message_err"):
        raise ValueError(f"授权失败：{data['message_err']}")
    if not data.get("refresh_token"):
        raise ValueError("授权结果里没有 refresh_token，请重新授权。")
    return save(mount, kind, {"refresh_token": data["refresh_token"]}, enabled=True, create=create)


@functools.cache
def logos() -> frozenset[str]:
    """引导页和来源列表的网盘图标：static/icons/drives/ 下已有的 SVG 文件名（不含扩展名），缺的用文字标记代替。"""
    from services import paths

    return frozenset(path.stem for path in (paths.APP_STATIC / "icons" / "drives").glob("*.svg"))
