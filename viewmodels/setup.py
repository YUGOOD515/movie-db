"""首次引导页的网盘选择：按品牌合并同一家网盘的几种登录方式，常用的排在前面。"""

from __future__ import annotations

# kinds 第一个是默认登录方式（能扫码的都默认扫码），第二个是“其他登录方式”里可以改用的类型。
# 本机目录和 AList 不是网盘，用图标代替 logo；mark 是没有 logo 文件时显示的文字标记。
# short 是图标下的短名（格子与图标同宽，左边缘对齐正文），name 给读屏软件。
DRIVE_BRANDS = (
    {"id": "quark", "name": "夸克网盘", "short": "夸克", "kinds": ("quark_tv", "quark"), "alt": "改用 Cookie 登录", "mark": "夸克"},
    {"id": "baidu", "name": "百度网盘", "short": "百度", "kinds": ("baidu",), "mark": "百度"},
    {"id": "aliyun", "name": "阿里云盘", "short": "阿里", "kinds": ("aliyun",), "mark": "阿里"},
    {"id": "uc", "name": "UC 网盘", "short": "UC", "kinds": ("uc_tv", "uc"), "alt": "改用 Cookie 登录", "mark": "UC"},
    {"id": "pan115", "name": "115 网盘", "short": "115", "kinds": ("pan115",), "mark": "115"},
    {"id": "pan123", "name": "123 云盘", "short": "123", "kinds": ("pan123_web", "pan123"), "alt": "改用开放平台凭据", "mark": "123"},
    {"id": "onedrive", "name": "OneDrive", "short": "OneDrive", "kinds": ("onedrive",), "mark": "One"},
    {"id": "gdrive", "name": "Google Drive", "short": "Google", "kinds": ("gdrive",), "mark": "G"},
    {"id": "webdav", "name": "WebDAV", "short": "WebDAV", "kinds": ("webdav",), "mark": "DAV"},
    {"id": "local", "name": "本机文件夹", "short": "本机", "kinds": ("local",), "icon": "monitor"},
    {"id": "alist", "name": "AList / OpenList", "short": "AList", "kinds": ("alist",), "alt": "改为从 AList 导入网盘", "icon": "network"},
)


def build_drive_picker(drive_types: list[dict], logos: set[str]) -> list[dict]:
    """只保留能新建的类型；某个品牌的默认类型不能新建时整个品牌不显示。"""
    creatable = {item["kind"] for item in drive_types if item["creatable"]}
    brands = []
    for brand in DRIVE_BRANDS:
        kinds = [kind for kind in brand["kinds"] if kind in creatable]
        if not kinds or kinds[0] != brand["kinds"][0]:
            continue
        brands.append({**brand, "kinds": kinds, "alt": brand.get("alt", "") if len(kinds) > 1 or brand["id"] == "alist" else "",
                       "logo": f"icons/drives/{brand['id']}.svg" if brand["id"] in logos else ""})
    return brands
