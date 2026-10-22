"""检查安装包里的 Python 运行时：依赖可导入、瘦身后的 Pillow 仍能缩图与取色、窗口组件齐全。

用安装包自带的 Python 运行（CI 冒烟测试）：

    <runtime>/python.exe packaging/check_runtime.py <app 目录>
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
from pathlib import Path


def main() -> int:
    app_dir = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
    sys.path.insert(0, str(app_dir))
    failures = []

    for name in ("flask", "guessit", "PIL.Image", "requests", "dotenv", "pystray", "webview"):
        try:
            importlib.import_module(name)
        except Exception as error:  # noqa: BLE001 — 逐项报告
            failures.append(f"无法导入 {name}：{error}")

    from PIL import Image, features

    for module in ("_avif", "_imagingft", "_imagingcms"):
        if importlib.util.find_spec(f"PIL.{module}") is not None:
            failures.append(f"PIL.{module} 应在打包时删除")
    if sys.platform == "darwin" and importlib.util.find_spec("Quartz") is not None:
        failures.append("Quartz 应在打包时删除")
    for codec in ("jpg", "zlib", "webp"):
        if not features.check(codec):
            failures.append(f"Pillow 缺少 {codec} 支持")

    from services import image_tones, thumbnails

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        photo = root / "still.jpg"
        Image.radial_gradient("L").convert("RGB").resize((1280, 720)).save(photo, quality=90)
        logo = root / "logo.png"
        Image.new("RGBA", (800, 300), (255, 200, 0, 128)).save(logo)
        if not thumbnails.generate_thumbnail(photo, root / "t" / "still.jpg", (480, 270)):
            failures.append("JPEG 缩图失败")
        if not thumbnails.generate_thumbnail(logo, root / "t" / "logo.png", (300, 120)):
            failures.append("PNG 缩图失败")
        tone = image_tones.compute_tone(photo)
        if not tone or not tone.get("blurhash") or not tone.get("color", "").startswith("#"):
            failures.append(f"取环境色失败：{tone}")

    import desktop

    icon = desktop.make_icon(64)
    if icon.size != (64, 64):
        failures.append("托盘图标生成失败")

    if sys.platform == "win32":
        lib = Path(importlib.import_module("webview").__file__).parent / "lib"
        for name in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll",
                     "runtimes/win-x64/native/WebView2Loader.dll"):
            if not (lib / name).is_file():
                failures.append(f"缺少 webview/lib/{name}")
    elif sys.platform == "darwin":
        for name in ("AppKit", "WebKit", "webview.platforms.cocoa"):
            try:
                importlib.import_module(name)
            except Exception as error:  # noqa: BLE001
                failures.append(f"无法导入 {name}：{error}")

    for failure in failures:
        print(f"失败：{failure}")
    if not failures:
        print("运行时检查通过：依赖可导入，缩图与取色正常，窗口组件齐全")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
