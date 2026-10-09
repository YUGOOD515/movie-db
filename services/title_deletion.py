"""Preview and confirm whole-work deletion with reference-aware local image cleanup."""

import hashlib
import json
import os
import re
import tempfile
from contextlib import closing
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from models.database import connect_database, connect_writable
from models.library import delete_library_titles
from queries.deletion import query_title_deletion


class DeletionChanged(ValueError):
    pass


def _safe_file(path, boundary):
    boundary = boundary.resolve()
    try:
        path.resolve().relative_to(boundary)
    except ValueError:
        return False
    # Do not follow junctions or symbolic links while cleaning assets.
    return not any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction())
                   for part in (path, *path.parents) if part != boundary and boundary in part.parents)


def _asset_paths(local_path, image_root):
    parts = str(local_path).replace("\\", "/").split("/")
    if len(parts) < 2 or parts[0] != "images" or any(part in ("", ".", "..") for part in parts):
        return []
    original = image_root.joinpath(*parts[1:])
    thumbnail_root = image_root.parent / "thumbnails"
    thumbnail = thumbnail_root.joinpath(*parts)
    return [(path, boundary) for path, boundary in ((original, image_root), (thumbnail, thumbnail_root))
            if _safe_file(path, boundary)]


def _key(path):
    return os.path.normcase(str(path.resolve()))


def _plan(db, title_id, image_root):
    image_root = Path(image_root).resolve()
    data = query_title_deletion(db, title_id)
    ids = set(data["ids"])
    candidates, shared = {}, set()
    shared_uses = {}
    protected_paths = set()
    for row in data["references"]:
        paths = _asset_paths(row["local_path"], image_root)
        if row["title_id"] in ids:
            if not paths:
                protected_paths.add(row["local_path"])
            for path, _ in paths:
                candidates[_key(path)] = path
        else:
            for path, _ in paths:
                key = _key(path)
                shared.add(key)
                shared_uses.setdefault(key, {})[row["work_id"]] = row["work_name"]
    # Include cached/unindexed files owned by the selected work, never a whole parent tree.
    for value in data["ids"]:
        if not re.fullmatch(r"tt[0-9]+", value):
            continue
        for folder, boundary in ((image_root / value, image_root),
                                 (image_root.parent / "thumbnails" / "images" / value, image_root.parent / "thumbnails")):
            if _safe_file(folder, boundary) and folder.is_dir():
                for path in folder.rglob("*"):
                    if _safe_file(path, boundary) and path.is_file():
                        candidates[_key(path)] = path
    remove, keep = [], []
    for key, path in sorted(candidates.items()):
        if path.is_file():
            (keep if key in shared else remove).append(path)
    records = data["records"]
    snapshot = {"ids": data["ids"], "records": records,
                "files": [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in remove],
                "shared": [(str(path), sorted(shared_uses[_key(path)].items())) for path in keep],
                "protected": sorted(protected_paths)}
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    assets = {}
    removed_keys, shared_keys = {_key(path) for path in remove}, {_key(path) for path in keep}
    for row in data["references"]:
        if row["title_id"] not in ids:
            continue
        local_path = row["local_path"]
        paths = _asset_paths(local_path, image_root)
        original = next((path for path, boundary in paths if boundary == image_root), None)
        assets[local_path] = {
            "exists": original is not None and original.is_file(),
            "status": "delete" if original is not None and _key(original) in removed_keys
                      else "shared" if original is not None and _key(original) in shared_keys else "keep",
            "uses": [{"title_id": tid, "name": name} for tid, name in
                     sorted(shared_uses.get(_key(original), {}).items())] if original is not None else [],
        }
    indexed = {_key(path) for row in data["references"] if row["title_id"] in ids
               for path, _ in _asset_paths(row["local_path"], image_root)}
    extra_images = []
    for path in remove + keep:
        if _key(path) not in indexed and path.is_relative_to(image_root):
            extra_images.append({"local_path": "images/" + path.relative_to(image_root).as_posix(),
                                 "status": "shared" if _key(path) in shared_keys else "delete",
                                 "uses": [{"title_id": tid, "name": name} for tid, name in
                                          sorted(shared_uses.get(_key(path), {}).items())]})
    plan = {"title": data["title"], "title_ids": data["ids"],
            "counts": {table: len(rows) for table, rows in records.items()},
            "image_count": len(remove), "shared_count": len(keep), "protected_count": len(protected_paths),
            "records": records, "assets": assets, "extra_images": extra_images, "collections": data["collections"]}
    return plan, fingerprint, remove


def _signer(secret):
    return URLSafeTimedSerializer(secret, salt="whole-title-deletion")


def preview_title_deletion(database_path, image_root, title_id, secret):
    with closing(connect_database(database_path)) as db, db:
        db.execute("BEGIN")
        plan, fingerprint, _ = _plan(db, title_id, image_root)
    plan["token"] = _signer(secret).dumps({"title_id": plan["title"]["title_id"], "fingerprint": fingerprint})
    return plan


def execute_title_deletion(database_path, image_root, title_id, token, secret):
    try:
        approved = _signer(secret).loads(token, max_age=300)
    except (BadSignature, SignatureExpired, TypeError) as error:
        raise DeletionChanged("确认已失效，请重新检查删除范围。") from error
    if approved.get("title_id") != title_id:
        raise DeletionChanged("确认的作品不一致，请重新检查。")
    moved = []
    warnings = []
    staging = None
    with closing(connect_writable(database_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            plan, fingerprint, paths = _plan(db, title_id, image_root)
            if fingerprint != approved.get("fingerprint"):
                raise DeletionChanged("作品或图片已发生变化，请检查更新后的范围并再次确认。")
            if paths:
                staging = Path(tempfile.mkdtemp(prefix=".title-delete-", dir=Path(image_root).resolve().parent))
                for index, path in enumerate(paths):
                    target = staging / str(index)
                    path.replace(target)
                    moved.append((path, target))
            delete_library_titles(db, plan["title_ids"])
            db.commit()
        except Exception:
            db.rollback()
            for original, staged in reversed(moved):
                staged.replace(original)
            if staging is not None:
                staging.rmdir()
            raise
    for _, staged in moved:
        try:
            staged.unlink()
        except OSError:
            warnings.append("部分图片暂存文件未能清理。")
    if moved:
        try:
            staging.rmdir()
        except OSError:
            pass
    return plan, bool(warnings)
