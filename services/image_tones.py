"""影像色调：为海报与背景图提取环境色和 BlurHash 占位图。

与缩略图一样属于可重建的派生数据，保存在 static/thumbnails/tones.json：
{"images/.../poster.jpg": {"color": "#3a2b4c", "blurhash": "...", "mtime": 0}}
页面用 color 渲染环境光晕，用 blurhash 在图片加载前显示模糊色块。
"""

from __future__ import annotations

import colorsys
import json
import logging
import math
import os
import threading
import uuid
from pathlib import Path, PurePosixPath

from services.thumbnails import DEFAULT_STATIC_ROOT, _file_stat, normalize_image_path

logger = logging.getLogger(__name__)

TONES_RELATIVE_PATH = "thumbnails/tones.json"
TONE_FOLDERS = {"posters", "backdrops"}
SAMPLE_SIZE = 32
BASE83 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~"

_lock = threading.Lock()
_cache: dict[Path, tuple[int | None, dict]] = {}


def wants_tone(local_path: str | None) -> bool:
    if not local_path:
        return False
    relative = normalize_image_path(local_path)
    return relative is not None and bool(TONE_FOLDERS & set(PurePosixPath(relative).parts[:-1]))


# --------------------------------------------------------------------------
# BlurHash 编码（算法见 https://github.com/woltapp/blurhash，MIT）
# --------------------------------------------------------------------------

def _srgb_to_linear(value: int) -> float:
    v = value / 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(value: float) -> int:
    v = max(0.0, min(1.0, value))
    if v <= 0.0031308:
        return min(255, int(v * 12.92 * 255 + 0.5))
    return min(255, int((1.055 * v ** (1 / 2.4) - 0.055) * 255 + 0.5))


def _base83(value: int, length: int) -> str:
    return "".join(BASE83[(value // 83 ** (length - 1 - i)) % 83] for i in range(length))


def encode_blurhash(image, components_x: int, components_y: int) -> str:
    """image 为 RGB 模式的 Pillow 图像（建议先缩到 32px 左右）。"""
    width, height = image.size
    raw = image.convert("RGB").tobytes()
    pixels = [tuple(_srgb_to_linear(c) for c in raw[i:i + 3]) for i in range(0, len(raw), 3)]
    cos_x = [[math.cos(math.pi * i * x / width) for x in range(width)] for i in range(components_x)]
    cos_y = [[math.cos(math.pi * j * y / height) for y in range(height)] for j in range(components_y)]
    factors = []
    for j in range(components_y):
        for i in range(components_x):
            normalisation = 1 if i == 0 and j == 0 else 2
            r = g = b = 0.0
            for y in range(height):
                row = y * width
                cy = cos_y[j][y]
                for x in range(width):
                    basis = cos_x[i][x] * cy
                    pr, pg, pb = pixels[row + x]
                    r += basis * pr
                    g += basis * pg
                    b += basis * pb
            scale = normalisation / (width * height)
            factors.append((r * scale, g * scale, b * scale))

    dc, ac = factors[0], factors[1:]
    result = _base83((components_x - 1) + (components_y - 1) * 9, 1)
    if ac:
        actual_max = max(abs(v) for factor in ac for v in factor)
        quantised_max = max(0, min(82, math.floor(actual_max * 166 - 0.5)))
        maximum = (quantised_max + 1) / 166
        result += _base83(quantised_max, 1)
    else:
        maximum = 1
        result += _base83(0, 1)
    result += _base83(
        (_linear_to_srgb(dc[0]) << 16) + (_linear_to_srgb(dc[1]) << 8) + _linear_to_srgb(dc[2]), 4
    )
    for factor in ac:
        quantised = [
            max(0, min(18, math.floor(math.copysign(abs(v / maximum) ** 0.5, v) * 9 + 9.5)))
            for v in factor
        ]
        result += _base83(quantised[0] * 19 * 19 + quantised[1] * 19 + quantised[2], 2)
    return result


# --------------------------------------------------------------------------
# 环境色：取画面中占比高且有色彩的主色，再限制明度，保证深色界面上可读
# --------------------------------------------------------------------------

def ambient_color(image) -> str:
    from PIL import Image

    palette_image = image.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    palette = palette_image.getpalette() or []
    best, best_score = (40, 40, 48), -1.0
    for count, index in palette_image.getcolors() or []:
        r, g, b = palette[index * 3: index * 3 + 3]
        hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        # 偏好有饱和度、明度适中的颜色；接近纯黑 / 纯白的区域权重降低。
        score = count * (0.25 + saturation) * (1 - abs(lightness - 0.45) * 1.2)
        if score > best_score:
            best, best_score = (r, g, b), score
    hue, lightness, saturation = colorsys.rgb_to_hls(*(c / 255 for c in best))
    lightness = min(max(lightness, 0.3), 0.52)
    saturation = min(saturation, 0.75)
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def compute_tone(source_path: Path) -> dict | None:
    from PIL import Image, ImageOps

    try:
        with Image.open(source_path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode != "RGB":
                image = image.convert("RGBA").convert("RGB") if image.mode in ("P", "LA") else image.convert("RGB")
            image.draft("RGB", (SAMPLE_SIZE * 4, SAMPLE_SIZE * 4))
            sample = image.copy()
            sample.thumbnail((SAMPLE_SIZE, SAMPLE_SIZE), Image.Resampling.BILINEAR)
    except (OSError, ValueError) as error:
        logger.warning("Failed to read image tone for %s: %s", source_path, error)
        return None
    width, height = sample.size
    components = (4, 3) if width >= height else (3, 4)
    return {"color": ambient_color(sample), "blurhash": encode_blurhash(sample, *components)}


# --------------------------------------------------------------------------
# 存储
# --------------------------------------------------------------------------

def tones_path(static_root: str | Path | None = None) -> Path:
    return Path(static_root or DEFAULT_STATIC_ROOT) / TONES_RELATIVE_PATH


def load_tones(static_root: str | Path | None = None) -> dict:
    """读取色调表；按文件修改时间缓存，文件不存在时返回空表。"""
    path = tones_path(static_root)
    stat_result = _file_stat(path)
    mtime = stat_result.st_mtime_ns if stat_result else None
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    data = {}
    if mtime is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            logger.warning("Failed to read %s: %s", path, error)
    _cache[path] = (mtime, data)
    return data


def _write_tones(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def ensure_tones(local_paths, static_root: str | Path | None = None) -> int:
    """为缺失或已过期的图片计算色调，返回新写入的数量。"""
    root = Path(static_root or DEFAULT_STATIC_ROOT)
    pending = {}
    current = load_tones(root)
    for local_path in sorted(set(local_paths)):
        if not wants_tone(local_path):
            continue
        relative = normalize_image_path(local_path)
        source_stat = _file_stat(root / Path(*relative.parts))
        if source_stat is None:
            continue
        existing = current.get(local_path)
        if existing and existing.get("mtime") == source_stat.st_mtime_ns:
            continue
        tone = compute_tone(root / Path(*relative.parts))
        if tone:
            pending[local_path] = {**tone, "mtime": source_stat.st_mtime_ns}
    if not pending:
        return 0
    with _lock:
        data = dict(load_tones(root))
        data.update(pending)
        _write_tones(tones_path(root), data)
    return len(pending)


def public_tones(static_root: str | Path | None = None) -> dict:
    """只读快照使用的精简表：{local_path: [color, blurhash]}。"""
    return {
        path: [tone["color"], tone["blurhash"]]
        for path, tone in load_tones(static_root).items()
        if tone.get("color") and tone.get("blurhash")
    }


def start_backfill(database_path: str | Path, static_root: str | Path | None = None) -> None:
    """后台补齐现有海报和背景图的色调，不阻塞应用启动。"""

    if not Path(database_path).is_file():
        return

    def run():
        import sqlite3
        from contextlib import closing

        try:
            with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as db:
                paths = [
                    row[0]
                    for row in db.execute(
                        "SELECT local_path FROM title_artwork"
                        " WHERE artwork_type IN ('poster', 'landscape_poster')"
                    )
                ]
            written = ensure_tones(paths, static_root)
            if written:
                logger.info("Image tones generated: %d", written)
        except Exception as error:  # noqa: BLE001 - 后台任务只记录日志
            logger.warning("Image tone backfill failed: %s", error)

    threading.Thread(target=run, name="image-tones", daemon=True).start()
