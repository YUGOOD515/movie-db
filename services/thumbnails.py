"""Lightweight thumbnail cache generation and path resolution service.

Original images in static/images/ are the source of truth.
Thumbnails in static/thumbnails/ are disposable, derived assets.
"""

from __future__ import annotations

import logging
import os
import stat
import uuid
from contextlib import nullcontext
from pathlib import Path, PurePosixPath

from services import paths

logger = logging.getLogger(__name__)

# Default static root resolution：海报与缩略图在数据目录的 static/ 下
DEFAULT_STATIC_ROOT = paths.MEDIA_ROOT

# Standard target resolutions (preserving ~2x DPR for responsive display)
STILL_SIZE = (560, 315)        # 16:9 ratio for ~270x152 episode cards
BACKDROP_CARD_SIZE = (760, 428) # 16:9 ratio for ~380x214 backdrop cards
LOGO_CARD_SIZE = (320, 180)    # Max width 320 for header/card logos

# TMDB 的 original 常为 4K（单张数 MB），超出页面的显示需要：下载后限宽重存，从不放大。
ORIGINAL_MAX_WIDTH = {"still": 1920, "backdrop": 2560, "logo": 1000}


def image_kind(local_path: str) -> str | None:
    """still / backdrop / logo；海报与演员照返回 None。"""
    normalized = local_path.replace("\\", "/").lower()
    parts = PurePosixPath(normalized).parts
    filename = parts[-1] if parts else ""
    if "stills" in parts or "-still." in filename:
        return "still"
    if "backdrops" in parts:
        return "backdrop"
    if "logos" in parts:
        return "logo"
    return None


def get_target_size_for_path(local_path: str) -> tuple[int, int] | None:
    """Determine target thumbnail dimensions based on image path and kind."""
    # Posters (500x750) and hero backdrops are not downscaled for cards
    return {"still": STILL_SIZE, "backdrop": BACKDROP_CARD_SIZE, "logo": LOGO_CARD_SIZE}.get(image_kind(local_path))


def shrink_original(path: str | Path, local_path: str, quality: int = 88) -> bool:
    """把超过 ORIGINAL_MAX_WIDTH 的原图按比例缩小后原地替换；返回是否改写了文件。

    宽度在上限以内的图片保持原样（不重复压缩）；失败时保留原文件。
    """
    from PIL import Image, ImageOps

    limit = ORIGINAL_MAX_WIDTH.get(image_kind(local_path))
    if limit is None:
        return False
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.shrink")
    try:
        with Image.open(path) as image:
            if image.width <= limit:
                return False
            save_format = image.format
            if save_format not in ("JPEG", "PNG", "WEBP"):
                return False
            options = {"icc_profile": image.info["icc_profile"]} if image.info.get("icc_profile") else {}
            image = ImageOps.exif_transpose(image)
            if save_format == "JPEG":
                if image.mode != "RGB":
                    image = image.convert("RGB")
                options.update(quality=quality, optimize=True, progressive=True)
            else:
                if image.mode not in ("RGB", "RGBA", "L", "LA"):
                    image = image.convert("RGBA")
                options.update({"optimize": True} if save_format == "PNG" else {"quality": quality})
            size = (limit, max(1, round(image.height * limit / image.width)))
            image.resize(size, Image.Resampling.LANCZOS).save(temporary, format=save_format, **options)
        os.replace(temporary, path)
        return True
    except Exception as error:
        logger.warning("Failed to shrink %s: %s", local_path, error)
        return False
    finally:
        temporary.unlink(missing_ok=True)


def normalize_image_path(local_path: str) -> PurePosixPath | None:
    """Return a confined static/images path, or None for an unsafe path."""
    path = PurePosixPath(local_path.replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or path.parts[0] != "images"
        or any(
            part in ("", ".", "..") or ":" in part or "\0" in part
            for part in path.parts
        )
    ):
        return None
    return path


_SIZE_CACHE: dict[tuple[str, int], tuple[int, int] | None] = {}


def image_size(relative: str | None, *roots: str | Path) -> tuple[int, int] | None:
    """静态图片的固有宽高（供 <img width height> 预留位置，避免加载后页面跳动）。

    relative 是相对 static 的路径，依次在 roots 里找；SVG 读 viewBox，其余用 Pillow 只读文件头。
    按“路径 + 修改时间”缓存；路径不安全、文件不存在或读不出尺寸时返回 None（模板就不写尺寸）。
    """
    if not relative:
        return None
    path = PurePosixPath(str(relative).replace("\\", "/"))
    if path.is_absolute() or any(part in ("", ".", "..") or ":" in part for part in path.parts):
        return None
    for root in roots:
        target = Path(root) / Path(*path.parts)
        info = _file_stat(target)
        if info is None:
            continue
        key = (str(target), info.st_mtime_ns)
        if key not in _SIZE_CACHE:
            _SIZE_CACHE[key] = _read_size(target)
        return _SIZE_CACHE[key]
    return None


def _read_size(target: Path) -> tuple[int, int] | None:
    import re

    try:
        if target.suffix.lower() == ".svg":
            head = target.read_text(encoding="utf-8", errors="ignore")[:2000]
            box = re.search(r"""viewBox\s*=\s*["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)""", head)
            if not box:
                return None
            width, height = float(box.group(1)), float(box.group(2))
        else:
            from PIL import Image

            with Image.open(target) as image:
                width, height = image.size
    except (OSError, ValueError, ImportError):
        return None
    return (round(width), round(height)) if width > 0 and height > 0 else None


def _file_stat(path: Path):
    try:
        result = path.stat()
    except OSError:
        return None
    return result if stat.S_ISREG(result.st_mode) else None


def get_card_image_path(
    local_path: str | None,
    static_root: str | Path | None = None,
    thumbnail_index: frozenset[str] | set[str] | None = None,
) -> str | None:
    """Resolve the static relative path to use for a card image.

    Returns 'thumbnails/<local_path>' if valid thumbnail exists,
    otherwise gracefully falls back to '<local_path>'.
    thumbnail_index（只读快照提供的有效缩略图清单）存在时不访问文件系统。
    """
    if not local_path:
        return local_path

    target_size = get_target_size_for_path(local_path)
    if target_size is None:
        return local_path

    root = Path(static_root) if static_root else DEFAULT_STATIC_ROOT
    relative = normalize_image_path(local_path)
    if relative is None:
        return local_path
    thumb_rel = f"thumbnails/{relative.as_posix()}"
    if thumbnail_index is not None:
        return thumb_rel if thumb_rel in thumbnail_index else local_path
    thumb_path = root / "thumbnails" / Path(*relative.parts)
    orig_path = root / Path(*relative.parts)

    thumb_stat = _file_stat(thumb_path)
    source_stat = _file_stat(orig_path)
    if (
        thumb_stat is not None
        and source_stat is not None
        and thumb_stat.st_mtime_ns >= source_stat.st_mtime_ns
    ):
        return thumb_rel

    return local_path


def generate_thumbnail(
    source_path: Path,
    target_path: Path,
    target_size: tuple[int, int],
    quality: int = 85,
    *, source_image=None,
) -> bool:
    """Generate a single thumbnail atomically using Pillow.

    - Preserves aspect ratio with LANCZOS resampling.
    - Handles EXIF orientation.
    - Uses temporary file + atomic rename to prevent partial writes.
    """
    from PIL import Image, ImageOps

    try:
        source_path = Path(source_path)
        target_path = Path(target_path)

        if not source_path.is_file():
            return False

        target_path.parent.mkdir(parents=True, exist_ok=True)

        with (Image.open(source_path) if source_image is None else nullcontext(source_image.copy())) as img:
            img = ImageOps.exif_transpose(img)
            orig_w, orig_h = img.size

            # Avoid upscaling if source is already smaller
            is_png = source_path.suffix.lower() == ".png" or img.format == "PNG"

            if is_png:
                # Maintain transparency for PNG logos
                img.thumbnail(target_size, Image.Resampling.LANCZOS)
                output_img = img
                save_format = "PNG"
                save_kwargs = {"optimize": True}
            else:
                # 16:9 crop and fit for photo stills and backdrops
                if img.mode in ("RGBA", "LA", "P"):
                    # Convert transparent background to black/white or RGB
                    rgb_img = Image.new("RGB", img.size, (0, 0, 0))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    rgb_img.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                    img = rgb_img
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                if orig_w < target_size[0] or orig_h < target_size[1]:
                    img.thumbnail(target_size, Image.Resampling.LANCZOS)
                    output_img = img
                else:
                    output_img = ImageOps.fit(
                        img,
                        target_size,
                        method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.5),
                    )
                save_format = "JPEG"
                save_kwargs = {"quality": quality, "optimize": True}

            # Atomic save via temporary file in target directory
            temp_file = target_path.parent / f".tmp_{target_path.name}_{uuid.uuid4().hex[:8]}"
            try:
                output_img.save(temp_file, format=save_format, **save_kwargs)
                os.replace(temp_file, target_path)
                return True
            finally:
                if temp_file.exists():
                    temp_file.unlink(missing_ok=True)

    except Exception as error:
        logger.warning("Failed to generate thumbnail for %s: %s", source_path, error)
        return False


def ensure_thumbnail(
    local_path: str,
    static_root: str | Path | None = None,
    quality: int = 85,
) -> str:
    """Ensure thumbnail exists for the given local_path. Returns the resolved relative path."""
    if not local_path:
        return local_path

    target_size = get_target_size_for_path(local_path)
    if target_size is None:
        return local_path

    root = Path(static_root) if static_root else DEFAULT_STATIC_ROOT
    relative = normalize_image_path(local_path)
    if relative is None:
        return local_path
    source_path = root / Path(*relative.parts)
    thumb_rel = f"thumbnails/{relative.as_posix()}"
    target_path = root / "thumbnails" / Path(*relative.parts)

    source_stat = _file_stat(source_path)
    if source_stat is None:
        return local_path

    target_stat = _file_stat(target_path)
    if (
        target_stat is not None
        and target_stat.st_mtime_ns >= source_stat.st_mtime_ns
    ):
        return thumb_rel

    success = generate_thumbnail(source_path, target_path, target_size, quality=quality)
    return thumb_rel if success else local_path
