"""安装包瘦身：删掉依赖里 MovieDB 用不到、体积又大的部分（Windows 与 macOS 共用）。

- Pillow：只用来缩图、取环境色与画托盘图标（读写 JPEG / PNG / WebP）。AVIF 编解码（约 7.7 MB）、
  FreeType 字体渲染（约 2 MB）、色彩管理与 Tk / 数学 / 形态学扩展都没有用到；删除后 Pillow 会自动
  跳过对应格式。macOS 上再删掉只被这些扩展引用的动态库（如 libavif、libfreetype）。
- pywebview：只保留当前平台的后端与对应架构的 WebView2 组件；macOS 上它声明依赖、实际没有导入的
  pyobjc Quartz 绑定（约 1.5 MB）也删掉。

删除的清单都在这里，运行时检查见 packaging/check_runtime.py。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

PILLOW_UNUSED = ("_avif", "_imagingft", "_imagingcms", "_imagingtk", "_imagingmath", "_imagingmorph")
WEBVIEW_BACKENDS = {  # webview/platforms 下各平台需要的文件，其余删除
    "windows": {"__init__.py", "winforms.py", "edgechromium.py", "mshtml.py", "win32.py"},
    "macos": {"__init__.py", "cocoa.py"},
}


def _remove(path: Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_dir() and not path.is_symlink():
        size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())
        shutil.rmtree(path)
        return size
    size = path.lstat().st_size
    path.unlink()
    return size


def slim_pillow(site: Path, platform: str) -> int:
    pil = site / "PIL"
    if not pil.is_dir():
        return 0
    saved = 0
    for module in PILLOW_UNUSED:
        for path in pil.glob(f"{module}.*"):
            if path.suffix in (".pyd", ".so", ".pyi"):
                saved += _remove(path)
    if platform == "macos":
        saved += _prune_dylibs(pil / ".dylibs", pil)
    return saved


def _linked_names(binary: Path) -> set[str]:
    """Mach-O 文件引用的动态库文件名（用 otool；不在 macOS 上时返回空集，不做裁剪）。"""
    output = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True).stdout
    return {Path(line.strip().split(" (", 1)[0]).name for line in output.splitlines()[1:] if line.strip()}


def _prune_dylibs(folder: Path, pil: Path) -> int:
    """删除不再被任何扩展模块（直接或间接）引用的动态库。"""
    if not folder.is_dir() or not shutil.which("otool"):
        return 0
    dylibs = {path.name: path for path in folder.iterdir() if path.is_file()}
    needed: set[str] = set()
    pending = [path for path in pil.glob("*.so")]
    while pending:
        for name in _linked_names(pending.pop()):
            if name in dylibs and name not in needed:
                needed.add(name)
                pending.append(dylibs[name])
    return sum(_remove(path) for name, path in dylibs.items() if name not in needed)


def slim_webview(site: Path, platform: str, arch: str = "x64") -> int:
    webview = site / "webview"
    if not webview.is_dir():
        return 0
    saved = _remove(webview / "__pyinstaller")
    platforms = webview / "platforms"
    keep = WEBVIEW_BACKENDS[platform]
    for path in list(platforms.iterdir()) if platforms.is_dir() else []:
        if path.name not in keep and path.name != "__pycache__":
            saved += _remove(path)
    lib = webview / "lib"
    if platform == "macos":
        saved += _remove(lib)  # 全是 Windows 的 WebView2 组件与 Android 的 jar
        saved += _remove(site / "Quartz")
        for path in site.glob("pyobjc_framework_quartz-*.dist-info"):
            saved += _remove(path)
    else:
        saved += _remove(lib / "pywebview-android.jar")
        for path in (lib / "runtimes").glob("win-*"):
            if path.name != f"win-{arch}":
                saved += _remove(path)
        for path in lib.glob("WebBrowserInterop.*.dll"):
            if not re.search(rf"\.{arch}\.dll$", path.name):
                saved += _remove(path)
    return saved


def slim_site_packages(site: Path, platform: str, arch: str = "x64") -> int:
    # bin/ 是 pip 生成的命令行脚本（flask、bottle.py 等），安装包里用不到
    return _remove(site / "bin") + slim_pillow(site, platform) + slim_webview(site, platform, arch)
