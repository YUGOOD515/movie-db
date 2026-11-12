from __future__ import annotations

import requests

from connectors.drives import DRIVERS
from connectors.drives.base import DriveError
from models import drive_store
from services import media_source


def types():
    has_passthrough = drive_store.get_drive("*") is not None
    return [{"kind": kind, "label": driver.label, "fields": driver.fields}
            for kind, driver in DRIVERS.items() if kind != "alist" or not has_passthrough]


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
    result["saved"] = {name: bool(row["config"].get(name)) for name in _secret_fields(row["kind"])}
    if not row["enabled"]:
        result["status_state"], result["status_text"] = "off", "已停用"
    elif not row["status"]:
        result["status_state"], result["status_text"] = "warn", "尚未检测"
    else:
        connected = row["status"].startswith("已连接")
        result["status_state"] = "ok" if connected else "fail"
        result["status_text"] = "已连接" if connected else "检测失败"
    # 网盘页展示：透传挂载 "*" 没有可读的名字，按用途命名；详情只在比状态标签多出信息时显示。
    is_passthrough = row["mount"] == "*"
    result["display_name"] = media_source.PASSTHROUGH_NAME if is_passthrough else row["mount"]
    result["icon"] = "folder" if row["kind"] == "local" else "network"
    result.update(_route_view(row, passthrough_names, passthrough))
    if result["route_state"] == "delegated":
        # 路径已交给透传：标签说明“谁在提供”，比本网盘的连接状态更重要
        result["status_state"], result["status_text"] = "off", "已移交透传"
    status = (row["status"] or "").strip()
    # 停用后旧的检测结果已不代表当前状态，不再显示
    result["status_detail"] = status if row["enabled"] and status and status != result["status_text"] else ""
    return result


def _route_view(row, passthrough_names, passthrough):
    """网盘页上的路由说明：这个前缀现在由谁提供、透传里是否也有同名目录、能否移交透传。"""
    if row["mount"] == "*":
        delegated = sorted(item["mount"] for item in drive_store.list_drives()
                           if item.get("delegated") and media_source.route(item["mount"])[0] == "*")
        scope = "未匹配挂载的路径" + (f"，以及 {'、'.join('/' + mount for mount in delegated)}" if delegated else "")
        return {"scope": scope, "route_state": "self", "can_delegate": False, "overlap": False}
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
    return {"scope": f"/{row['mount']} · {text}", "route_state": state, "route_reason": reason,
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


def _route_message(changed):
    if not changed:
        return ""
    names = "、".join("其余路径" if key == "*" else f"/{key}" for key in changed)
    return f"{names} 的来源已变化，已开始重新扫描。"


def missing_mounts(paths):
    mounts = {path.strip("/").split("/", 1)[0] for path in paths if path and path != "/"}
    configured = {row["mount"] for row in drive_store.list_drives() if row["enabled"]}
    return sorted(mounts - configured) if "*" not in configured else []


def _error(error):
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


def save(mount, kind, values, enabled=None, create=False):
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
            raise ValueError(f"请填写{field.label}。")
        changed = changed or config[field.name] != previous_value
    if changed:
        config = {field.name: config[field.name] for field in DRIVERS[kind].fields if field.name in config}
    candidate = dict(config)
    driver = _driver(kind, candidate, lambda updated: candidate.update(updated))
    try:
        status = driver.check()
    except Exception as error:
        raise DriveError(_error(error)) from None
    before = routes()
    drive_store.save_drive(mount, kind, candidate, enabled)
    drive_store.set_status(mount, status)
    media_source.forget()
    changed = rerouted(before)
    return {"ok": True, "status": status, "rerouted": changed, "message": _route_message(changed)}


def test(mount):
    row = drive_store.get_drive(mount)
    if not row:
        return {"ok": False, "message": "找不到这个挂载。"}
    try:
        status = _driver(row["kind"], row["config"], lambda config: drive_store.update_config(mount, config)).check()
    except Exception as error:
        message = _error(error)
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
    changed = rerouted(before)
    return {"ok": True, "enabled": not row["enabled"], "rerouted": changed, "message": _route_message(changed)}


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
    changed = rerouted(before)
    return {"ok": True, "delegated": delegated, "rerouted": changed, "message": _route_message(changed)}


def delete(mount):
    before = routes()
    drive_store.delete_drive(mount)
    media_source.forget()
    changed = rerouted(before)
    return {"ok": True, "rerouted": changed, "message": _route_message(changed)}
