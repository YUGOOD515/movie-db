"""从 AList / OpenList 导入网盘挂载：先预览每个存储会怎样处理，确认后再写入。

挂载名取 AList 挂载路径的第一级（/Quark → Quark），与库内已有媒体路径一致，导入后无需重新关联。
确认导入时按路径重新读取并映射，不信任页面传回的配置；每个挂载照常经 drives.save 检测后才保存，
原来移交给透传的同名路径改回由内置驱动提供。
"""

from __future__ import annotations

from connectors.drives import DRIVERS
from connectors.drives.alist_config import read_storages
from connectors.drives.base import DriveError
from models import drive_store
from services import drives, media_source


def _text(addition, *keys):
    for key in keys:
        value = addition.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _quark(addition):
    return {"cookie": _text(addition, "cookie"), "root_id": _text(addition, "root_folder_id") or "0"}, ""


def _aliyun(addition):
    config = {
        "refresh_token": _text(addition, "refresh_token"),
        "drive_type": _text(addition, "drive_type") or "resource",
        "alipan_type": _text(addition, "alipan_type") or "default",
        "client_id": _text(addition, "client_id"),
        "client_secret": _text(addition, "client_secret"),
        "root_id": _text(addition, "root_folder_id") or "root",
    }
    # AList 的 oauth_token_url 与 OpenList 的 api_url_address 都是同一套 GET 续期协议
    renew = _text(addition, "oauth_token_url", "api_url_address")
    if renew:
        config["renew_api"] = renew
    return config, ""


def _baidu(addition):
    config = {
        "refresh_token": _text(addition, "refresh_token"),
        "client_id": _text(addition, "client_id"),
        "client_secret": _text(addition, "client_secret"),
        "root_path": _text(addition, "root_folder_path") or "/",
    }
    renew = _text(addition, "api_url_address")
    if renew and not config["client_id"]:
        config["renew_api"] = renew
    return config, ""


def _pan123(addition):
    root = _text(addition, "root_folder_id") or "0"
    client_id, client_secret = _text(addition, "client_id"), _text(addition, "client_secret")
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret, "root_id": root}, ""
    refresh = _text(addition, "refresh_token")
    renew = _text(addition, "api_url_address")
    if refresh and renew:
        return {"refresh_token": refresh, "renew_api": renew, "root_id": root}, ""
    # AList 令牌模式的 refresh_token 由 AList 自己的应用签发，MovieDB 无法续期，只能先用 access_token
    return {"access_token": _text(addition, "access_token"), "root_id": root}, "access_token 约 90 天到期，到期后需在网盘页更新"


def _local(addition):
    return {"root": _text(addition, "root_folder_path")}, ""


def _pan123_web(addition):
    return {"username": _text(addition, "username"), "password": _text(addition, "password"),
            "root_id": _text(addition, "root_folder_id") or "0"}, ""


def _quark_tv(addition):
    return {"refresh_token": _text(addition, "refresh_token"), "device_id": _text(addition, "device_id"),
            "root_id": _text(addition, "root_folder_id") or "0"}, ""


def _webdav(addition):
    if _text(addition, "vendor") == "sharepoint":
        return None, "SharePoint 方式的 WebDAV 暂不支持"
    return {"url": _text(addition, "address"), "username": _text(addition, "username"),
            "password": _text(addition, "password"), "root_path": _text(addition, "root_folder_path") or "/"}, ""


def _oauth(addition, config):
    """AList 原版直接用 client_id / client_secret 续期；OpenList 开启 use_online_api 时走在线续期地址。"""
    if addition.get("use_online_api") and _text(addition, "api_url_address"):
        config["renew_api"] = _text(addition, "api_url_address")
    elif _text(addition, "client_id") and _text(addition, "client_secret"):
        config.update(client_id=_text(addition, "client_id"), client_secret=_text(addition, "client_secret"))
    return config


def _onedrive(addition):
    if addition.get("is_sharepoint"):
        return None, "SharePoint 站点暂不支持"
    config = {"refresh_token": _text(addition, "refresh_token"), "region": _text(addition, "region") or "global",
              "root_path": _text(addition, "root_folder_path") or "/"}
    if _text(addition, "redirect_uri"):
        config["redirect_uri"] = _text(addition, "redirect_uri")
    return _oauth(addition, config), ""


def _gdrive(addition):
    config = {"refresh_token": _text(addition, "refresh_token"), "root_id": _text(addition, "root_folder_id") or "root"}
    return _oauth(addition, config), ""


def _pan115(addition):
    return {"refresh_token": _text(addition, "refresh_token"), "access_token": _text(addition, "access_token"),
            "root_id": _text(addition, "root_folder_id") or "0"}, ""


# AList 驱动名（meta.go 中的 Name）→ (MovieDB 网盘类型, 字段映射)
MAPPERS = {
    "Quark": ("quark", _quark),
    "UC": ("uc", _quark),
    "AliyundriveOpen": ("aliyun", _aliyun),
    "BaiduNetdisk": ("baidu", _baidu),
    "123 Open": ("pan123", _pan123),
    "123Open": ("pan123", _pan123),
    "Local": ("local", _local),
    "123Pan": ("pan123_web", _pan123_web),
    "QuarkTV": ("quark_tv", _quark_tv),
    "UCTV": ("uc_tv", _quark_tv),
    "WebDav": ("webdav", _webdav),
    "Onedrive": ("onedrive", _onedrive),
    "GoogleDrive": ("gdrive", _gdrive),
    "115 Open": ("pan115", _pan115),
}

ACTION_LABELS = {"create": "新建", "update": "更新凭据", "skip": "跳过"}


def _credentials_present(kind, config):
    driver = DRIVERS[kind]
    required = [field.name for field in driver.fields if field.required]
    if kind == "pan123":
        return any(config.get(name) for name in ("refresh_token", "access_token", "client_id"))
    if kind in ("quark_tv", "uc_tv"):
        return bool(config.get("refresh_token"))
    return all(config.get(name) for name in required)


def _plan(storage, existing, passthrough):
    """一个 AList 存储的处理方式。返回给页面的字段不含任何凭据。"""
    mount_path = storage["mount_path"].strip()
    mount = mount_path.strip("/")
    item = {"mount": mount or mount_path or "/", "mount_path": mount_path, "driver": storage["driver"],
            "kind": "", "label": storage["driver"], "action": "skip", "reason": "", "note": ""}
    mapper = MAPPERS.get(storage["driver"])
    if mapper:
        item["kind"] = mapper[0]
        item["label"] = DRIVERS[mapper[0]].label
    if storage["disabled"]:
        item["reason"] = "在 AList 中已停用"
    elif not mount or "/" in mount or mount in (".", ".."):
        item["reason"] = "MovieDB 只支持一级挂载名，请在网盘页手动添加"
    elif not mapper:
        item["reason"] = (f"MovieDB 没有内置这种网盘，继续由{media_source.PASSTHROUGH_NAME}提供"
                          if passthrough else "MovieDB 暂不支持这种网盘")
    else:
        config, note = mapper[1](storage["addition"])
        current = existing.get(mount)
        if config is None:
            item["reason"] = note
        elif not _credentials_present(item["kind"], config):
            item["reason"] = "AList 中没有可用的凭据"
        elif current and current["kind"] != item["kind"]:
            item["reason"] = f"已有同名的{DRIVERS[current['kind']].label}挂载"
        elif current:
            item["action"] = "update"
            extra = []
            if current.get("delegated"):
                extra.append("改回由本网盘提供")
            elif not current["enabled"]:
                extra.append("启用")
            item["reason"] = "已有同名挂载，" + "并".join(["更新凭据", *extra])
        else:
            item["action"] = "create"
            item["reason"] = "新建挂载"
        if config is not None:
            item["note"], item["config"] = note, config
    item["action_label"] = ACTION_LABELS[item["action"]]
    return item


def _plans(location):
    existing = {row["mount"]: row for row in drive_store.list_drives()}
    passthrough = existing.get("*")
    return [_plan(storage, existing, passthrough) for storage in read_storages(location)]


def _public(item):
    return {key: value for key, value in item.items() if key != "config"}


def preview(location):
    items = _plans(location)
    return {"ok": True, "items": [_public(item) for item in items],
            "importable": sum(item["action"] != "skip" for item in items)}


def apply(location, mounts):
    wanted = {str(mount) for mount in mounts or []}
    plans = [item for item in _plans(location) if item["action"] != "skip" and item["mount"] in wanted]
    if not plans:
        return {"ok": False, "message": "没有可以导入的挂载。"}
    before = drives.routes()
    results = []
    for item in plans:
        mount = item["mount"]
        try:
            drives.save(mount, item["kind"], item["config"], enabled=True, create=item["action"] == "create")
            if (drive_store.get_drive(mount) or {}).get("delegated"):
                drives.delegate(mount, False)
            results.append({"mount": mount, "ok": True, "message": item["reason"], "note": item["note"]})
        except (DriveError, ValueError) as error:
            results.append({"mount": mount, "ok": False, "message": str(error)})
    changed = drives.rerouted(before)
    imported = [result["mount"] for result in results if result["ok"]]
    message = f"已导入 {len(imported)} 个挂载" if imported else "导入失败"
    if len(imported) < len(results):
        message += f"，{len(results) - len(imported)} 个未能导入"
    return {"ok": bool(imported), "results": results, "rerouted": changed, "message": message + "。",
            "route_message": drives.route_message(changed), "alist_unused": _alist_unused(location)}


def _alist_unused(location):
    """AList 里启用的存储是否都已由内置驱动接管（之后可以停掉 AList、停用透传）。"""
    rows = {row["mount"]: row for row in drive_store.list_drives()}
    for storage in read_storages(location):
        if storage["disabled"]:
            continue
        row = rows.get(storage["mount_path"].strip("/"))
        if not row or not row["enabled"] or row.get("delegated"):
            return False
    return True
