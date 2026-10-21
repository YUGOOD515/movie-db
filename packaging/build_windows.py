"""组装 Windows 安装包：嵌入式 Python + 依赖 + ffprobe + 应用代码，再用 NSIS 打包。

    uv run python packaging/build_windows.py              # 输出到 dist/
    uv run python packaging/build_windows.py --no-ffprobe # 不内置 ffprobe（安装包约小 45MB）

Windows 与 Linux 上都能组装（依赖按 Windows 平台下载二进制 wheel）；有 makensis 时生成
MovieDB-Setup-<版本>.exe，否则只生成免安装的 zip。下载的文件缓存在 .cache/packaging/。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = os.environ.get("MOVIEDB_PYTHON_VERSION", "3.13.16")
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
FFMPEG_RELEASE = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
FFMPEG_FALLBACK = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                   "ffmpeg-n8.1-latest-win64-lgpl-shared-8.1.zip")
# 只取 ffprobe 及其依赖的动态库；LGPL 版本可随软件分发。
FFPROBE_FILES = re.compile(r"(ffprobe\.exe|(avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale)-\d+\.dll)$", re.I)
EXTRA_PACKAGES = ["pystray==0.19.5"]  # 托盘图标，仅桌面版需要
# 不随安装包分发的仓库内容：开发资料、测试与示例数据。
EXCLUDED = ("tests/", "archive/", "packaging/", "scratch/", ".planning/", ".claude/", ".github/", "data/",
            "media_names.json", "AGENTS.MD", "uv.lock", ".gitignore", ".python-version")


def log(message: str) -> None:
    print(message, flush=True)


def download(url: str, target: Path) -> Path:
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"下载 {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "moviedb-packager"})
    temporary = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as file:
        shutil.copyfileobj(response, file, 1024 * 1024)
    temporary.replace(target)
    return target


def ffmpeg_url() -> str:
    """BtbN 每日构建的最新 LGPL 共享库版本；查询失败时用固定地址。"""
    if os.environ.get("MOVIEDB_FFMPEG_URL"):
        return os.environ["MOVIEDB_FFMPEG_URL"]
    headers = {"User-Agent": "moviedb-packager", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(FFMPEG_RELEASE, headers=headers), timeout=30) as response:
            assets = json.load(response).get("assets", [])
    except (OSError, ValueError):
        return FFMPEG_FALLBACK
    pattern = re.compile(r"ffmpeg-n(\d+)\.(\d+)-latest-win64-lgpl-shared-[\d.]+\.zip$")
    releases = [(tuple(map(int, match.groups())), asset["browser_download_url"])
                for asset in assets if (match := pattern.search(asset.get("name", "")))]
    return max(releases)[1] if releases else FFMPEG_FALLBACK


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def uv() -> str:
    found = shutil.which("uv") or str(Path(sys.executable).parent / ("uv.exe" if os.name == "nt" else "uv"))
    if not Path(found).exists() and not shutil.which(found):
        sys.exit("需要 uv：https://docs.astral.sh/uv/")
    return found


def install_python(target: Path, cache: Path) -> None:
    archive = download(PYTHON_URL, cache / f"python-{PYTHON_VERSION}-embed-amd64.zip")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    tag = "".join(PYTHON_VERSION.split(".")[:2])
    # 嵌入版默认不读 site-packages；._pth 指定模块搜索路径（相对 python.exe 所在目录）。
    (target / f"python{tag}._pth").write_text(
        f"python{tag}.zip\n.\nLib\\site-packages\n..\\..\\app\nimport site\n", encoding="utf-8")


def install_packages(target: Path, cache: Path) -> None:
    requirements = cache / "requirements-windows.txt"
    exported = subprocess.run([uv(), "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project",
                               "--no-header", "--no-annotate"], cwd=ROOT, check=True, capture_output=True,
                              text=True).stdout
    requirements.write_text(exported + "\n".join(EXTRA_PACKAGES) + "\n", encoding="utf-8")
    log("安装 Windows 依赖")
    subprocess.run([uv(), "pip", "install", "--quiet", "-r", str(requirements), "--target", str(target),
                    "--python-platform", "x86_64-pc-windows-msvc", "--python-version",
                    ".".join(PYTHON_VERSION.split(".")[:2]), "--only-binary", ":all:", "--no-compile"],
                   cwd=ROOT, check=True)
    for path in list(target.rglob("__pycache__")):
        shutil.rmtree(path, ignore_errors=True)


def install_ffprobe(target: Path, cache: Path) -> str:
    url = ffmpeg_url()
    archive = download(url, cache / Path(url).name)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            name = member.rsplit("/", 1)[-1]
            if FFPROBE_FILES.search(name) or name == "LICENSE.txt":
                (target / name).write_bytes(bundle.read(member))
    if not (target / "ffprobe.exe").exists():
        sys.exit(f"{archive.name} 中没有 ffprobe.exe")
    (target / "SOURCE.txt").write_text(
        f"ffprobe 来自 FFmpeg（LGPL 2.1+）：{url}\n源码：https://github.com/FFmpeg/FFmpeg\n", encoding="utf-8")
    return Path(url).name


def tracked_files() -> list[str]:
    try:
        output = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
        files = [name for name in output.decode("utf-8").split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        sys.exit("需要在 Git 仓库中运行（只打包已纳入版本管理的文件，避免带上本机数据）。")
    return [name for name in files if not name.startswith(EXCLUDED) and (ROOT / name).is_file()]


def copy_app(target: Path) -> int:
    count = 0
    for name in tracked_files():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
        count += 1
    return count


def write_icon(path: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from desktop import make_icon

    make_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def write_uninstall_list(app: Path, target: Path) -> Path:
    """卸载时“保留数据”用的清单：逐个删除安装进来的程序文件，再由深到浅删除空目录。"""
    files = sorted(path.relative_to(app) for path in app.rglob("*") if path.is_file())
    folders = sorted({parent for path in files for parent in path.parents if parent != Path(".")},
                     key=lambda path: len(path.parts), reverse=True)
    lines = [f'Delete "$INSTDIR\\app\\{path}"' for path in map(_windows, files)]
    for folder in map(_windows, folders):  # 运行时生成的 __pycache__ 也属于程序文件
        lines += [f'RMDir /r "$INSTDIR\\app\\{folder}\\__pycache__"', f'RMDir "$INSTDIR\\app\\{folder}"']
    lines += ['RMDir /r "$INSTDIR\\app\\__pycache__"', 'RMDir "$INSTDIR\\app"']
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _windows(path: Path) -> str:
    return str(path).replace("/", "\\")


def folder_size(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "packaging")
    parser.add_argument("--no-ffprobe", action="store_true", help="不内置 ffprobe")
    parser.add_argument("--zip", action="store_true", help="同时生成免安装 zip（没有 makensis 时总会生成）")
    args = parser.parse_args()

    version = project_version()
    stage = args.out / "MovieDB"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    cache = args.cache.resolve()

    install_python(stage / "runtime" / "python", cache)
    install_packages(stage / "runtime" / "python" / "Lib" / "site-packages", cache)
    ffmpeg = None if args.no_ffprobe else install_ffprobe(stage / "runtime" / "ffmpeg", cache)
    count = copy_app(stage / "app")
    write_icon(stage / "moviedb.ico")
    (stage / "MovieDB.cmd").write_text(  # 免安装版的启动方式；安装版使用开始菜单快捷方式
        '@echo off\r\nstart "" "%~dp0runtime\\python\\pythonw.exe" "%~dp0app\\desktop.py"\r\n', encoding="utf-8")
    (stage / "VERSION.txt").write_text(f"MovieDB {version}\nPython {PYTHON_VERSION}\nffprobe {ffmpeg or '未内置'}\n",
                                       encoding="utf-8")

    parts = {name: folder_size(stage / name) for name in ("runtime/python", "runtime/ffmpeg", "app")}
    log(f"应用文件 {count} 个；Python 与依赖 {megabytes(parts['runtime/python'])}，"
        f"ffprobe {megabytes(parts['runtime/ffmpeg']) if ffmpeg else '未内置'}，应用 {megabytes(parts['app'])}，"
        f"合计 {megabytes(folder_size(stage))}")

    outputs = []
    makensis = shutil.which("makensis") or next(
        (str(path) for path in (Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "NSIS" / "makensis.exe",
                                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NSIS" / "makensis.exe")
         if path.exists()), None)
    if makensis:
        installer = args.out / f"MovieDB-Setup-{version}.exe"
        filelist = write_uninstall_list(stage / "app", args.out / "uninstall_files.nsh")
        subprocess.run([makensis, "-V2", "-INPUTCHARSET", "UTF8", f"-DVERSION={version}", f"-DSTAGE={stage.resolve()}",
                        f"-DOUTFILE={installer.resolve()}", f"-DFILELIST={filelist.resolve()}",
                        *([] if ffmpeg else ["-DNO_FFPROBE"]),
                        str(ROOT / "packaging" / "moviedb.nsi")], check=True)
        outputs.append(installer)
    if args.zip or not makensis:
        archive = args.out / f"MovieDB-{version}-portable"
        outputs.append(Path(shutil.make_archive(str(archive), "zip", args.out, "MovieDB")))
    for output in outputs:
        log(f"输出 {output}（{megabytes(output.stat().st_size)}）")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write("### MovieDB Windows 安装包\n\n| 文件 | 大小 |\n|---|---|\n")
            file.writelines(f"| {output.name} | {megabytes(output.stat().st_size)} |\n" for output in outputs)
            file.write(f"| 安装后占用 | {megabytes(folder_size(stage))} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
