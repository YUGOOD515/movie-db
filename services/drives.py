from __future__ import annotations

import requests

from connectors.drives import DRIVERS
from connectors.drives.base import DriveError
from models import drive_store
from services import media_source


def types():
    return [{"kind": kind, "label": driver.label, "fields": driver.fields}
            for kind, driver in DRIVERS.items()]


def _secret_fields(kind):
    return tuple(field.name for field in DRIVERS[kind].fields if field.kind == "secret")


def public(row):
    result = drive_store.public_drive(row, _secret_fields(row["kind"]))
    result["label"] = DRIVERS.get(row["kind"], type("Unknown", (), {"label": row["kind"]})).label
    result["saved"] = {name: bool(row["config"].get(name)) for name in _secret_fields(row["kind"])}
    return result


def listing():
    return [public(row) for row in drive_store.list_drives()]


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


def save(mount, kind, values, enabled=True):
    mount = "*" if kind == "alist" else str(mount or "").strip()
    if not mount or "/" in mount or mount in (".", ".."):
        raise ValueError("请填写有效的挂载名，且不要包含 /。")
    if kind not in DRIVERS:
        raise ValueError("不支持的网盘类型。")
    previous = drive_store.get_drive(mount) or {}
    config = dict(previous.get("config") or {})
    for field in DRIVERS[kind].fields:
        value = values.get(field.name)
        if value is None:
            value = config.get(field.name, field.default)
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if field.kind == "secret" and not value and previous:
            continue
        config[field.name] = value or field.default
        if field.required and not config[field.name]:
            raise ValueError(f"请填写{field.label}。")
    candidate = dict(config)
    driver = _driver(kind, candidate, lambda updated: candidate.update(updated))
    try:
        status = driver.check()
    except Exception as error:
        raise DriveError(_error(error)) from None
    drive_store.save_drive(mount, kind, candidate, enabled)
    drive_store.set_status(mount, status)
    media_source.forget(mount)
    return {"ok": True, "status": status}


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
    drive_store.save_drive(mount, row["kind"], row["config"], not row["enabled"])
    media_source.forget(mount)
    return {"ok": True, "enabled": not row["enabled"]}


def delete(mount):
    drive_store.delete_drive(mount)
    media_source.forget(mount)
    return {"ok": True}
