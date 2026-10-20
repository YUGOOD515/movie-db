"""把渲染所需的应用代码复制到 build/，供 pywrangler 打包。

只复制只读渲染路径会用到的模块与模板；外部接口与写入服务在 app.py 中按需导入，
只读模式下不会被加载。
"""

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILD = HERE / "build"
FILES = ["app.py"]
PACKAGES = ["models", "queries", "viewmodels", "templates"]
SERVICES = ["__init__.py", "image_tones.py", "playback.py", "thumbnails.py"]


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir()
    shutil.copy2(HERE / "worker.py", BUILD / "worker.py")
    for name in FILES:
        shutil.copy2(ROOT / name, BUILD / name)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in PACKAGES:
        shutil.copytree(ROOT / name, BUILD / name, ignore=ignore)
    (BUILD / "services").mkdir()
    for name in SERVICES:
        shutil.copy2(ROOT / "services" / name, BUILD / "services" / name)
    print(f"Built renderer sources in {BUILD}")


if __name__ == "__main__":
    main()
