"""生成只读快照并增量同步到 Cloudflare。

本地应用在数据库变化后自动在子进程中运行本脚本，也可手动执行：

    uv run python scripts/publish_snapshot.py            # 渲染并上传
    uv run python scripts/publish_snapshot.py --dry-run  # 只渲染，输出统计

流程：备份 library.db → 以只读模式导入同一套 Flask 应用 → 爬取全部浏览页面
（含详情、单集面板与常用筛选组合）→ 与云端清单比对，只上传有变化的页面、
静态文件、图片与数据库 → 提交新清单 → 删除过期对象。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import sqlite3
import sys
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from services import paths  # noqa: E402

STATIC_ROOT = BASE_DIR / "static"  # 随程序发布的样式、脚本与图标
MEDIA_ROOT = paths.MEDIA_ROOT  # 海报与缩略图（数据目录；源码运行时与 STATIC_ROOT 相同）
DEFAULT_SYNC_URL = "https://moviedb-router.ccpcudati.workers.dev"
MAX_PAGES = 20000
MAX_KEY_BYTES = 1000
UPLOAD_WORKERS = 8
SEED_PATHS = (
    "/",
    "/recent",
    "/unwatched",
    "/top",
    "/catalog",
    "/catalog?type=movie",
    "/catalog?type=series",
    "/favorites",
    "/media",
    "/media?tab=associate",
)
ATTRIBUTE_PATH = re.compile(r'=\s*"(/[^"\s]*)"')
FORM_PATTERN = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S | re.I)
TAG_ATTRIBUTES = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')


def page_key(path_and_query: str) -> str:
    """与云端 Worker 一致的页面键：去掉空参数并按名称排序。"""
    parts = urlsplit(path_and_query)
    pairs = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if value != ""
    )
    query = urlencode(pairs, quote_via=quote, safe="")
    return "page" + (parts.path or "/") + (f"?{query}" if query else "")


def load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(paths.ENV_PATH, override=False)


def backup_database(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def thumbnail_index(static_root: Path) -> list[str]:
    """有效缩略图：源图仍存在，且缩略图不早于源图（与本地解析规则一致）。"""
    thumbnails = static_root / "thumbnails"
    valid = []
    if not thumbnails.is_dir():
        return valid
    for thumb in thumbnails.rglob("*"):
        if not thumb.is_file() or thumb.name.startswith(".tmp_"):
            continue
        relative = thumb.relative_to(thumbnails)
        source = static_root / relative
        try:
            if thumb.stat().st_mtime_ns >= source.stat().st_mtime_ns:
                valid.append(f"thumbnails/{relative.as_posix()}")
        except OSError:
            continue
    return sorted(valid)


def _form_variants(page_html: str, url_map_adapter, read_only_endpoints) -> list[tuple[str, str]]:
    """GET 自动提交筛选表单：为每个字段的每个可选值生成一个相邻地址。"""
    variants = []
    for attributes, body in FORM_PATTERN.findall(page_html):
        form_attributes = dict(TAG_ATTRIBUTES.findall(attributes))
        if form_attributes.get("method", "get").lower() != "get" or "data-auto-submit" not in attributes:
            continue
        action = html.unescape(form_attributes.get("action", ""))
        if not action.startswith("/"):
            continue
        fields: dict[str, str] = {}
        options: dict[str, list[str]] = {}
        for tag in re.findall(r"<(?:input|button)\b[^>]*>", body, re.I):
            tag_attributes = {
                name: html.unescape(value) for name, value in TAG_ATTRIBUTES.findall(tag)
            }
            name = tag_attributes.get("name")
            input_type = tag_attributes.get("type", "").lower()
            if "data-filter-set" in tag_attributes:
                key = tag_attributes["data-filter-set"]
                options.setdefault(key, []).append(tag_attributes.get("data-filter-value", ""))
            elif name and input_type == "hidden":
                fields[name] = tag_attributes.get("value", "")
            elif name and input_type == "radio":
                options.setdefault(name, []).append(tag_attributes.get("value", ""))
                fields.setdefault(name, "")
                if re.search(r"\schecked\b", tag):
                    fields[name] = tag_attributes.get("value", "")
        for name, values in options.items():
            for value in values:
                if fields.get(name, "") == value:
                    continue
                state = dict(fields)
                state[name] = value
                variants.append((name, f"{action}?{urlencode(state)}"))
    return variants


def render_pages(app, database: Path) -> dict[str, bytes]:
    from app import READ_ONLY_ENDPOINTS

    adapter = app.url_map.bind("localhost")
    seeds = list(SEED_PATHS)
    connection = sqlite3.connect(database)
    try:
        seeds += [f"/title/{row[0]}" for row in connection.execute("SELECT title_id FROM title_basic")]
        seeds += [
            f"/title/{row[0]}/episode-panel"
            for row in connection.execute("SELECT title_id FROM title_basic WHERE title_type = 'episode'")
        ]
        seeds += [f"/collection/{row[0]}" for row in connection.execute("SELECT collection_id FROM collection")]
    finally:
        connection.close()

    pages: dict[str, bytes] = {}
    seen: set[str] = set()
    queue = deque((seed, ()) for seed in seeds)
    client = app.test_client()
    while queue and len(pages) < MAX_PAGES:
        url, form_changes = queue.popleft()
        key = page_key(url)
        if key in seen or len(key.encode()) > MAX_KEY_BYTES:
            continue
        seen.add(key)
        try:
            endpoint, _ = adapter.match(urlsplit(url).path, method="GET")
        except Exception:
            continue
        if endpoint == "static" or endpoint not in READ_ONLY_ENDPOINTS:
            continue
        response = client.get(url)
        if response.status_code != 200 or not response.mimetype == "text/html":
            continue
        body = response.get_data()
        pages[key] = body
        text = body.decode("utf-8", "replace")
        for match in ATTRIBUTE_PATH.findall(text):
            target = html.unescape(match)
            if not target.startswith("//") and not target.startswith("/static/"):
                queue.append((target.split("#", 1)[0], ()))
        if not form_changes or form_changes == ("type",):
            for field, variant in _form_variants(text, adapter, READ_ONLY_ENDPOINTS):
                if field in form_changes:
                    continue
                queue.append((variant, form_changes + (field,)))
    return pages


def static_files() -> dict[str, Path]:
    files = {}
    separate = MEDIA_ROOT.resolve() != STATIC_ROOT.resolve()
    for root in (STATIC_ROOT, MEDIA_ROOT) if separate else (STATIC_ROOT,):
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if not path.is_file() or path.name.startswith("."):
                continue
            if separate and paths.is_media_path(relative) != (root == MEDIA_ROOT):
                continue  # 程序目录里的旧图片、数据目录里的非图片都不发布
            files["static/" + relative] = path
    return files


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SyncClient:
    def __init__(self, base_url: str, token: str):
        import requests

        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs):
        for attempt in range(4):
            try:
                response = self.session.request(method, self.base_url + path, timeout=120, **kwargs)
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
            except Exception:
                if attempt == 3:
                    raise
            time.sleep(2 ** attempt)
        response.raise_for_status()
        return response

    def manifest(self) -> dict:
        return self._request("GET", "/_sync/manifest").json().get("manifest", {})

    def put(self, key: str, data: bytes, content_type: str, digest: str) -> None:
        self._request("PUT", "/_sync/object", data=data, headers={
            "X-Object-Key": quote(key, safe=""),
            "Content-Type": content_type,
            "X-Object-Sha256": digest,
        })

    def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 500):
            self._request("POST", "/_sync/delete", json={"keys": keys[start:start + 500]})

    def commit(self, manifest: dict, meta: dict) -> None:
        self._request("POST", "/_sync/commit", json={"manifest": manifest, "meta": meta})


def build_snapshot(source_database: Path, workdir: Path) -> tuple[dict, dict]:
    """返回 (objects, meta)；objects 为 键 → (bytes 或 Path, content_type)。"""
    database = workdir / "library.db"
    backup_database(source_database, database)
    snapshot_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    thumbnails = thumbnail_index(MEDIA_ROOT)

    os.environ["MOVIEDB_READ_ONLY"] = "1"
    os.environ["MOVIEDB_DATABASE"] = str(database)
    os.environ["MOVIEDB_SNAPSHOT_AT"] = snapshot_at
    sys.path.insert(0, str(BASE_DIR))
    import app as movie_app

    from services.image_tones import public_tones

    tones = public_tones(MEDIA_ROOT)
    movie_app.app.config["THUMBNAIL_INDEX"] = frozenset(thumbnails)
    movie_app.app.config["IMAGE_TONES"] = tones
    pages = render_pages(movie_app.app, database)

    objects: dict[str, tuple[object, str]] = {
        key: (body, "text/html; charset=utf-8") for key, body in pages.items()
    }
    for key, path in static_files().items():
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        objects[key] = (path, content_type)
    objects["data/library.db"] = (database.read_bytes(), "application/vnd.sqlite3")
    meta = {
        "snapshot_at": snapshot_at,
        "alist_origin": movie_app.app.config["ALIST_ORIGIN"],
        "thumbnails": thumbnails,
        "tones": tones,
        "pages": len(pages),
    }
    return objects, meta


def _read(value) -> bytes:
    return value.read_bytes() if isinstance(value, Path) else value


def publish(dry_run: bool = False) -> dict:
    load_env_file()
    source = Path(os.environ.get("MOVIEDB_SOURCE_DATABASE") or paths.database_path())
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="moviedb-snapshot-") as directory:
        objects, meta = build_snapshot(source, Path(directory))
        manifest = {}
        for key, (value, content_type) in objects.items():
            data = _read(value)
            manifest[key] = {"sha": sha256(data), "size": len(data), "type": content_type}
        meta["version"] = sha256(json.dumps(manifest, sort_keys=True).encode())[:16]
        summary = {
            "pages": meta["pages"],
            "objects": len(manifest),
            "bytes": sum(item["size"] for item in manifest.values()),
            "version": meta["version"],
        }
        if dry_run:
            summary["seconds"] = round(time.monotonic() - started, 1)
            return summary

        token = os.environ.get("MOVIEDB_SYNC_TOKEN", "").strip()
        if not token:
            raise SystemExit("未设置 MOVIEDB_SYNC_TOKEN，无法同步快照。")
        client = SyncClient(os.environ.get("MOVIEDB_SYNC_URL") or DEFAULT_SYNC_URL, token)
        remote = client.manifest()
        changed = [
            key for key, item in manifest.items()
            if (remote.get(key) or {}).get("sha") != item["sha"]
        ]

        def upload(key: str) -> None:
            value, content_type = objects[key]
            client.put(key, _read(value), content_type, manifest[key]["sha"])

        with ThreadPoolExecutor(UPLOAD_WORKERS) as pool:
            list(pool.map(upload, changed))
        client.commit(manifest, meta)
        stale = sorted(set(remote) - set(manifest))
        if stale:
            client.delete(stale)
        summary.update(uploaded=len(changed), deleted=len(stale),
                       seconds=round(time.monotonic() - started, 1))
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="只渲染并统计，不上传")
    arguments = parser.parse_args()
    print(json.dumps(publish(dry_run=arguments.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
