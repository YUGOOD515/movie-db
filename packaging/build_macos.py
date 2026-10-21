"""组装 macOS 版：MovieDB.app（内置 Python + 依赖 + ffprobe + 应用代码），再做成 .dmg。

    uv run python packaging/build_macos.py                      # 本机架构，输出到 dist/
    uv run python packaging/build_macos.py --arch x86_64        # Intel 版（在 Apple 芯片上也能组装）
    uv run python packaging/build_macos.py --ffprobe-dir <目录> # 内置 build_ffprobe.sh 编译的精简版 ffprobe

Python 来自 python-build-standalone（与 uv 使用的是同一套），依赖按 macOS 平台下载二进制 wheel。
只有在 macOS 上才能编译启动器、签名并生成 .dmg；在其他系统上只组装 .app 目录，用于检查内容。
没有 Apple 开发者证书，所以只做临时（ad-hoc）签名：首次打开时需要在“系统设置 → 隐私与安全性”
中点“仍要打开”。
"""

from __future__ import annotations

import argparse
import os
import plistlib
import platform
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_windows import (  # noqa: E402  共用：下载缓存、依赖导出、应用文件清单
    EXTRA_PACKAGES, ROOT, copy_app, download, folder_size, log, megabytes, project_version, uv,
)

PYTHON_VERSION = os.environ.get("MOVIEDB_PYTHON_VERSION", "3.13.16")
PBS_RELEASE = os.environ.get("MOVIEDB_PBS_RELEASE", "20261020")
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/{release}/"
           "cpython-{version}%2B{release}-{triple}-install_only_stripped.tar.gz")
TRIPLES = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}
MINIMUM_MACOS = "11.0"
BUNDLE_ID = "io.github.yugood515.moviedb"
IS_MACOS = sys.platform == "darwin"


def install_python(target: Path, arch: str, cache: Path) -> None:
    url = PBS_URL.format(release=PBS_RELEASE, version=PYTHON_VERSION, triple=TRIPLES[arch])
    archive = download(url, cache / f"cpython-{PYTHON_VERSION}-{PBS_RELEASE}-{TRIPLES[arch]}.tar.gz")
    extract = target.parent / "python-extract"
    shutil.rmtree(extract, ignore_errors=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(extract, filter="tar")  # 保留 bin/python3 等符号链接与可执行权限
    shutil.move(str(extract / "python"), str(target))
    extract.rmdir()
    # 不需要的部分：开发头文件、测试、IDLE 与 Tk（MovieDB 不用图形界面库）
    minor = ".".join(PYTHON_VERSION.split(".")[:2])
    library = target / "lib" / f"python{minor}"
    for path in (target / "include", target / "share", target / "lib" / "pkgconfig", library / "test",
                 library / "idlelib", library / "tkinter", library / "turtledemo", library / "ensurepip",
                 library / "site-packages" / "pip"):
        shutil.rmtree(path, ignore_errors=True)
    # bin/python3.13 是静态链接的，libpython 只供嵌入使用；其余脚本（pip、idle、pydoc）也用不到
    for path in [*(target / "lib").glob("libpython*.dylib"), *(target / "bin").iterdir(),
                 *(library / "site-packages").glob("pip-*.dist-info")]:
        if path.name not in ("python3", f"python{minor}"):
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    for path in [*library.glob("config-*"), *(target / "lib").glob("libtcl*"), *(target / "lib").glob("libtk*"),
                 *(target / "lib").glob("tcl*"), *(target / "lib").glob("tk*"), *(target / "lib").glob("itcl*"),
                 *(target / "lib").glob("thread*"), *library.glob("lib-dynload/_tkinter*")]:
        shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)


def site_packages(python: Path) -> Path:
    return python / "lib" / f"python{'.'.join(PYTHON_VERSION.split('.')[:2])}" / "site-packages"


def install_packages(target: Path, arch: str, cache: Path) -> None:
    requirements = cache / "requirements-macos.txt"
    exported = subprocess.run([uv(), "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project",
                               "--no-header", "--no-annotate"], cwd=ROOT, check=True, capture_output=True,
                              text=True).stdout
    requirements.write_text(exported + "\n".join(EXTRA_PACKAGES) + "\n", encoding="utf-8")
    log(f"安装 macOS（{arch}）依赖")
    subprocess.run([uv(), "pip", "install", "--quiet", "-r", str(requirements), "--target", str(target),
                    "--python-platform", TRIPLES[arch], "--python-version",
                    ".".join(PYTHON_VERSION.split(".")[:2]), "--only-binary", ":all:", "--no-compile"],
                   cwd=ROOT, check=True, env={**os.environ, "MACOSX_DEPLOYMENT_TARGET": MINIMUM_MACOS})
    for path in [*target.rglob("__pycache__"), target / "PyObjCTest"]:  # pyobjc-core 自带的测试
        shutil.rmtree(path, ignore_errors=True)


def install_ffprobe(target: Path, source: Path) -> str:
    if not (source / "ffprobe").is_file():
        sys.exit(f"{source} 中没有 ffprobe")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("ffprobe", "LICENSE.txt", "SOURCE.txt"):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    (target / "ffprobe").chmod(0o755)
    first = (source / "SOURCE.txt").read_text(encoding="utf-8").splitlines()[0] \
        if (source / "SOURCE.txt").is_file() else ""
    version = first.split("FFmpeg ", 1)[1].split("（", 1)[0] if "FFmpeg " in first else ""
    return f"精简版 {version}".strip()


def precompile(python_root: Path, app: Path) -> None:
    """预先编译字节码：.app 里不能在运行时写文件（会破坏签名），没有 .pyc 启动会慢。"""
    minor = tuple(int(part) for part in PYTHON_VERSION.split(".")[:2])
    if sys.version_info[:2] != minor:
        log(f"跳过预编译：需要用 Python {PYTHON_VERSION} 运行本脚本（当前 {platform.python_version()}）")
        return
    import compileall
    import py_compile

    quiet_ok = True
    for folder in (python_root / "lib", app):
        quiet_ok &= compileall.compile_dir(folder, quiet=2, workers=0, ddir=None,
                                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    if not quiet_ok:
        log("部分文件无法编译（通常是依赖包里的模板或示例），运行时会直接读取源码")


def build_launcher(target: Path, arch: str) -> None:
    source = ROOT / "packaging" / "macos" / "launcher.c"
    target.parent.mkdir(parents=True, exist_ok=True)
    if IS_MACOS:
        subprocess.run(["clang", "-O2", "-Wall", "-arch", arch, f"-mmacosx-version-min={MINIMUM_MACOS}",
                        "-o", str(target), str(source)], check=True)
        return
    # 非 macOS 只用于检查组装结果：用等价的 shell 脚本占位
    target.write_text('#!/bin/sh\ncontents="$(cd "$(dirname "$0")/.." && pwd)"\n'
                      'exec "$contents/Resources/runtime/python/bin/python3" -B -s -E '
                      '"$contents/Resources/app/desktop.py" "$@"\n', encoding="utf-8")
    target.chmod(0o755)


def write_icon(resources: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from desktop import make_icon

    icon = make_icon(1024)
    icon.save(resources / "MovieDB.icns", format="ICNS")


def write_info(contents: Path, version: str) -> None:
    info = {
        "CFBundleName": "MovieDB",
        "CFBundleDisplayName": "MovieDB",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "MovieDB",
        "CFBundleIconFile": "MovieDB",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleDevelopmentRegion": "zh_CN",
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "LSUIElement": True,  # 只在菜单栏显示图标，不占程序坞
        "LSApplicationCategoryType": "public.app-category.entertainment",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "YUGOOD515",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")


def _is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            magic = file.read(4)
    except OSError:
        return False
    return magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def sign(bundle: Path) -> None:
    """临时签名：先逐个签内置的可执行文件与动态库，再签整个 .app。"""
    binaries = [str(path) for path in (bundle / "Contents" / "Resources").rglob("*")
                if path.is_file() and not path.is_symlink() and _is_macho(path)]
    for start in range(0, len(binaries), 200):
        subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", *binaries[start:start + 200]],
                       check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)


def make_dmg(bundle: Path, output: Path) -> Path:
    staging = bundle.parent / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    subprocess.run(["ditto", str(bundle), str(staging / bundle.name)], check=True)
    (staging / "Applications").symlink_to("/Applications")
    output.unlink(missing_ok=True)
    command = ["hdiutil", "create", "-quiet", "-volname", "MovieDB", "-srcfolder", str(staging),
               "-fs", "HFS+", "-format", "UDZO", "-imagekey", "zlib-level=9", "-ov", str(output)]
    for attempt in range(3):  # hdiutil 偶尔报“资源忙”，稍等重试
        if subprocess.run(command).returncode == 0:
            break
        if attempt == 2:
            sys.exit("hdiutil 创建 .dmg 失败")
        time.sleep(5)
    shutil.rmtree(staging)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    native = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    parser.add_argument("--arch", choices=sorted(TRIPLES), default=native)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "packaging")
    parser.add_argument("--ffprobe-dir", type=Path,
                        help="build_ffprobe.sh 编译的精简版 ffprobe 所在目录；不指定时不内置 ffprobe")
    parser.add_argument("--no-dmg", action="store_true", help="只组装 MovieDB.app")
    args = parser.parse_args()

    version = project_version()
    work = args.out / f"macos-{args.arch}"
    bundle = work / "MovieDB.app"
    shutil.rmtree(work, ignore_errors=True)
    contents = bundle / "Contents"
    resources = contents / "Resources"
    resources.mkdir(parents=True)
    cache = args.cache.resolve()

    python = resources / "runtime" / "python"
    install_python(python, args.arch, cache)
    install_packages(site_packages(python), args.arch, cache)
    ffmpeg = install_ffprobe(resources / "runtime" / "ffmpeg", args.ffprobe_dir.resolve()) if args.ffprobe_dir else None
    count = copy_app(resources / "app")
    precompile(python, resources / "app")
    build_launcher(contents / "MacOS" / "MovieDB", args.arch)
    write_icon(resources)
    write_info(contents, version)
    (resources / "VERSION.txt").write_text(
        f"MovieDB {version}\nPython {PYTHON_VERSION}（python-build-standalone {PBS_RELEASE}）\n"
        f"ffprobe {ffmpeg or '未内置'}\n", encoding="utf-8")
    log(f"应用文件 {count} 个；Python 与依赖 {megabytes(folder_size(python))}，ffprobe "
        f"{megabytes(folder_size(resources / 'runtime' / 'ffmpeg')) if ffmpeg else '未内置'}，"
        f"合计 {megabytes(folder_size(bundle))}")

    outputs = []
    if IS_MACOS:
        sign(bundle)
        if not args.no_dmg:
            outputs.append(make_dmg(bundle, args.out / f"MovieDB-{version}-macOS-{args.arch}.dmg"))
    else:
        log("不是 macOS：已组装 MovieDB.app（启动器为脚本占位），跳过签名与 .dmg")
    for output in outputs:
        log(f"输出 {output}（{megabytes(output.stat().st_size)}）")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and outputs:
        with open(summary, "a", encoding="utf-8") as file:
            file.write(f"### MovieDB macOS（{args.arch}）\n\n| 文件 | 大小 |\n|---|---|\n")
            file.writelines(f"| {output.name} | {megabytes(output.stat().st_size)} |\n" for output in outputs)
            file.write(f"| 安装后占用 | {megabytes(folder_size(bundle))} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
