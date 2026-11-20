# 任务：Windows 和 macOS 打包脚本内置官方 mpv 播放器

## 背景

MovieDB 是 Flask 写的本地影视管理应用。`packaging/build_windows.py` 把嵌入式 Python、依赖、ffprobe 和应用代码组装到 `dist/MovieDB/`，再用 NSIS 打成安装包；`packaging/build_macos.py` 组装 `MovieDB.app` 并生成 .dmg（`build_macos.py` 会从 `build_windows.py` 导入 `download` 等工具函数）。

下一个版本要把 **mpv 官方 v0.42.0 发布包** 放进安装包，位置和 ffprobe 一样在 `runtime/` 下。播放器代码由别的任务负责，它会从下面这些位置找 mpv：

- Windows：`dist/MovieDB/runtime/mpv/mpv.exe`
- macOS：`MovieDB.app/Contents/Resources/runtime/mpv/mpv.app/Contents/MacOS/mpv`
- 配置目录（两个平台一样）：`<runtime>/mpv/config/mpv.conf`

仓库里已经准备好的文件（**不要改，不要删**）：

- `packaging/mpv/mpv.conf`：MovieDB 自带的 mpv 配置
- `packaging/mpv/LICENSE.GPL`、`packaging/mpv/Copyright`：mpv 的许可证和版权说明

先通读 `packaging/build_windows.py` 和 `packaging/build_macos.py`，再动手；特别看现有 ffprobe 是怎么处理的（`install_ffprobe`、`--no-ffprobe`、`VERSION.txt`、`-DNO_FFPROBE`），新代码照同样的风格写。

**你的沙箱不联网，不能真的下载 mpv。** 所有测试都用临时造的假 zip、假 tar，并替换掉 `download`。真实下载和打包由我来跑。

## 官方包实测数据（已确认，直接写进代码）

| 平台 | 地址 | SHA-256 |
|---|---|---|
| Windows x64 | `https://github.com/mpv-player/mpv/releases/download/v0.42.0/mpv-v0.42.0-x86_64-pc-windows-msvc.zip` | `8dd69cbab079086765fa5d873c09513b2834b85d146c1301718a757e8c0595ef` |
| macOS arm64 | `https://github.com/mpv-player/mpv/releases/download/v0.42.0/mpv-v0.42.0-macos-15-arm.zip` | `be1b8d2445e6880860869c080b8710dee189721f1fa4689840db6a2c10d7be69` |
| macOS x86_64 | `https://github.com/mpv-player/mpv/releases/download/v0.42.0/mpv-v0.42.0-macos-15-intel.zip` | `2d2f34350ff844b6014c35f21ca142937275e19c0c673150a8564fcd2f3bfa14` |

各个包的内容：

- Windows zip：5 个文件都在根目录（没有子目录）——`mpv.exe`、`vulkan-1.dll`、`mpv.com`、`mpv-register.bat`、`mpv-unregister.bat`。**只要前两个**。
- macOS zip：根目录只有一个 `mpv.tar.gz`。它解开后的所有条目都在 `mpv.app/` 下，结构是 `mpv.app/Contents/{Info.plist, MacOS/mpv, MacOS/lib/*.dylib, Frameworks/libMoltenVK.dylib, Resources/..., _CodeSignature/...}`，没有符号链接。**整个 `mpv.app` 都要保留**，不能删任何文件。

## 要改的文件

- `packaging/build_windows.py`
- `packaging/build_macos.py`
- `tests/test_packaging_mpv.py`（新建）

不要改其他文件（尤其是 `packaging/moviedb.nsi`、`.github/`、`connectors/`、`services/`、文档）。

## 接口契约

### `packaging/build_windows.py`

新增常量：

```python
MPV_VERSION = "0.42.0"
MPV_RELEASE = f"https://github.com/mpv-player/mpv/releases/download/v{MPV_VERSION}/"
MPV_WINDOWS_URL = MPV_RELEASE + f"mpv-v{MPV_VERSION}-x86_64-pc-windows-msvc.zip"
MPV_WINDOWS_SHA256 = "8dd69cbab079086765fa5d873c09513b2834b85d146c1301718a757e8c0595ef"
MPV_WINDOWS_FILES = ("mpv.exe", "vulkan-1.dll")
MPV_ASSETS = ROOT / "packaging" / "mpv"
```

新增函数（macOS 脚本也要用，所以放在这里）：

1. `sha256_of(path: Path) -> str`：按块读取文件，返回小写十六进制。
2. `verify_sha256(path: Path, expected: str, *, remove_on_mismatch: bool) -> str`：
   - 一致时返回实际值；
   - 不一致时，如果 `remove_on_mismatch` 为真就先删除这个文件（下载缓存坏了，下次重新下载），然后 `sys.exit(f"{path.name} 校验失败：期望 {expected}，实际 {actual}")`。
3. `write_mpv_extras(target: Path, source: str, sha256: str) -> None`：
   - 复制 `MPV_ASSETS/mpv.conf` 到 `target/config/mpv.conf`；
   - 复制 `MPV_ASSETS/LICENSE.GPL` 和 `MPV_ASSETS/Copyright` 到 `target/`；
   - 写 `target/SOURCE.txt`（UTF-8），内容严格是下面四行加一个结尾换行：
     ```
     mpv {MPV_VERSION}（GPLv2+，见 LICENSE.GPL 与 Copyright）
     下载：{source}
     SHA-256：{sha256}
     源码：https://github.com/mpv-player/mpv/tree/v{MPV_VERSION}
     ```
   - `source` 是下载地址；用户用 `--mpv-zip` 传了本地文件时，是该文件的文件名。
4. `install_mpv(target: Path, cache: Path, archive: Path | None = None) -> str`：
   - `archive` 为空时：`download(MPV_WINDOWS_URL, cache / <地址里的文件名>)`，校验时 `remove_on_mismatch=True`；
   - `archive` 不为空时：直接用它，校验时 `remove_on_mismatch=False`；
   - 校验通过后，`target.mkdir(parents=True, exist_ok=True)`；对 `MPV_WINDOWS_FILES` 里的每个名字，在 zip 里找**名字完全相同**的条目（只认根目录，`sub/mpv.exe` 不算），写到 `target/<名字>`；任何一个找不到就 `sys.exit`，提示里写出缺少的文件名；
   - **不要用 `extractall`**，zip 里的其他条目（包括 `../` 开头的）一律忽略；
   - 调用 `write_mpv_extras`；
   - 返回 `MPV_VERSION`。

`main()` 的改动：

- 新增参数 `--no-mpv`（不内置 mpv）和 `--mpv-zip <路径>`（用本地的官方 zip，CI 缓存和离线时用）。两个同时给时由 argparse 报错（用互斥组）。
- 在 ffprobe 之后调用：`mpv = None if args.no_mpv else install_mpv(stage / "runtime" / "mpv", cache, args.mpv_zip.resolve() if args.mpv_zip else None)`。
- `VERSION.txt` 在 ffprobe 那一行之后加一行：`mpv {mpv or '未内置'}`。
- `parts` 加上 `runtime/mpv`；体积日志在 ffprobe 之后加 `mpv <大小或“未内置”>`。
- 调用 makensis 时，没内置 mpv 就加 `-DNO_MPV`，写在 `-DNO_FFPROBE` 那一处的旁边，写法相同。
- `GITHUB_STEP_SUMMARY` 在“其中 Python 与依赖”之后，内置了 mpv 时加一行 `| 其中 mpv | <大小> |`。
- 模块说明文字补上 mpv 和两个新参数的用法。

### `packaging/build_macos.py`

新增常量：

```python
MPV_MACOS = {  # 架构 → (下载地址, SHA-256)
    "arm64": (MPV_RELEASE + f"mpv-v{MPV_VERSION}-macos-15-arm.zip",
              "be1b8d2445e6880860869c080b8710dee189721f1fa4689840db6a2c10d7be69"),
    "x86_64": (MPV_RELEASE + f"mpv-v{MPV_VERSION}-macos-15-intel.zip",
               "2d2f34350ff844b6014c35f21ca142937275e19c0c673150a8564fcd2f3bfa14"),
}
```

（`MPV_RELEASE`、`MPV_VERSION` 等从 `build_windows` 导入，加进现有的 import 列表。）

新增 `install_mpv(target: Path, cache: Path, arch: str, archive: Path | None = None) -> str`：

- 下载和校验规则跟 Windows 一样，用 `MPV_MACOS[arch]`；
- 从 zip 里取出名字完全等于 `mpv.tar.gz` 的条目，没有就 `sys.exit`；
- 用 `tarfile` 打开它，先检查：每个条目的名字（去掉开头的 `./`）都必须是 `mpv.app` 或者以 `mpv.app/` 开头，否则 `sys.exit`，提示里写出这个条目名；
- 然后 `extractall(target, filter="data")`（会保留可执行权限、挡住路径穿越）；
- 要求 `target/mpv.app/Contents/MacOS/mpv` 存在（不存在就 `sys.exit`），并把它的权限设成 `0o755`；
- 调用 `write_mpv_extras(target, ...)`，配置、许可证、SOURCE.txt 放在 `target/` 下，和 `mpv.app` 并列；
- 返回 `MPV_VERSION`；
- 这个函数在非 macOS 上也要能运行（只用 Python 标准库），因为非 macOS 上也会组装 .app 用来检查内容。

`main()` 的改动：参数 `--no-mpv` / `--mpv-zip`（互斥，含义同 Windows）；在 ffprobe 之后调用 `install_mpv(resources / "runtime" / "mpv", cache, args.arch, ...)`；`VERSION.txt` 加 `mpv {mpv or '未内置'}`；体积日志加 mpv。模块说明文字同步更新。

`sign(bundle)` 改成由内向外签名：

1. 和现在一样，逐个签 `Contents/Resources` 下的 Mach-O 文件，加上 `MovieDB-python`（每批最多 200 个）；
2. **新增**：找出 `Contents/Resources` 下所有名字以 `.app` 结尾的目录，按路径深度从深到浅，逐个 `codesign --force --sign - --timestamp=none <该目录>`；
3. 和现在一样，签整个 MovieDB.app，然后 `codesign --verify --deep --strict`。

## 测试（`tests/test_packaging_mpv.py`）

导入方式：`sys.path` 里加入 `packaging` 目录，然后 `import build_windows`、`import build_macos`。不联网：用 monkeypatch 把 `build_windows.download` 换掉（macOS 脚本导入的是同一个名字，也要换 `build_macos.download`）。把 `MPV_WINDOWS_SHA256`、`MPV_MACOS` 换成假包的实际哈希。

必须覆盖：

1. `verify_sha256`：不一致时抛 `SystemExit`，消息里同时有期望值和实际值；`remove_on_mismatch=True` 时文件被删除，为 `False` 时文件还在；一致时返回哈希值。
2. Windows `install_mpv`：假 zip 里放 `mpv.exe`、`vulkan-1.dll`、`mpv.com`、`mpv-register.bat`、`doc/readme.txt`、`../evil.txt`。安装后：
   - `target` 里的文件**正好**是 `mpv.exe`、`vulkan-1.dll`、`config/mpv.conf`、`LICENSE.GPL`、`Copyright`、`SOURCE.txt`；
   - `target` 的上一级目录里没有出现 `evil.txt`；
   - `mpv.exe` 的内容和假 zip 里的一样；
   - `SOURCE.txt` 的四行和契约一致；
   - 返回 `"0.42.0"`。
3. Windows：假 zip 里只有 `sub/mpv.exe` 和 `vulkan-1.dll` 时抛 `SystemExit`，消息里有 `mpv.exe`。
4. Windows：传 `archive` 时不调用 `download`，`SOURCE.txt` 的“下载：”一行是该文件的文件名；哈希不一致时这个文件不被删除。
5. Windows `main()`：用 monkeypatch 替换掉耗时和联网的步骤（`install_python`、`install_packages`、`install_ffprobe`、`copy_app`、`write_icon`、`make_launcher`、`write_uninstall_list`），让 makensis “存在”，并记录 `subprocess.run` 的调用：
   - 默认时：`install_mpv` 被调用；makensis 参数里没有 `-DNO_MPV`；`VERSION.txt` 里有 `mpv 0.42.0`；
   - `--no-mpv` 时：`install_mpv` 没被调用；makensis 参数里有 `-DNO_MPV`；`VERSION.txt` 里有 `mpv 未内置`；
   - `--no-mpv --mpv-zip x` 时：argparse 报错退出。
6. macOS `install_mpv`：假 zip 里放一个 `mpv.tar.gz`，tar 里有 `mpv.app/Contents/Info.plist`、`mpv.app/Contents/MacOS/mpv`（权限 0o755）、`mpv.app/Contents/MacOS/lib/libfoo.dylib`。安装后这三个文件都在，`config/mpv.conf`、`SOURCE.txt` 和 `mpv.app` 并列；非 Windows 上检查 `mpv` 有可执行权限（Windows 上跳过这一条断言）。
7. macOS：tar 里有 `other/file` 时抛 `SystemExit`；有 `mpv.app/../../evil` 时，不能在 `target` 外面生成文件（报错退出或被 filter 拦住都可以，但要断言外面没有文件）。
8. macOS：zip 里没有 `mpv.tar.gz`，或者 tar 里没有 `mpv.app/Contents/MacOS/mpv` 时，抛 `SystemExit`。
9. `sign()` 的顺序：造一个假的 `MovieDB.app`，在 `Resources/runtime/mpv/mpv.app/Contents/MacOS/` 下放 `mpv` 和 `lib/libfoo.dylib`（文件开头写 Mach-O 魔数 `b"\xcf\xfa\xed\xfe"`），另放 `Contents/MacOS/MovieDB-python`。替换 `subprocess.run` 记录调用，然后断言：
   - 签 `mpv.app` 那次调用排在所有含 `mpv` 和 `libfoo.dylib` 的调用之后；
   - 签 `MovieDB.app` 那次调用排在签 `mpv.app` 之后；
   - 最后一次调用是 `--verify --deep --strict`。

现有的测试全部通过，不要修改现有测试。

## 约束

- Python 3.13，只用标准库。代码要能在 Windows、macOS、Linux 上运行（打包脚本支持跨平台组装）。
- 风格跟随现有打包脚本：中文日志和中文注释，用已有的 `log`、`megabytes`、`folder_size`、`download`。
- 只做契约里写的改动，不要顺手重构，不要加其他配置项。

## 验收标准

1. 在工作树里运行 `.venv\Scripts\python.exe -m pytest -q tests/test_packaging_mpv.py`，全部通过；在 Windows 上只允许跳过第 6 项里的可执行权限断言。
2. `.venv\Scripts\python.exe -m pytest -q` 全部通过（整个测试套件）。
3. `.venv\Scripts\python.exe packaging/build_windows.py --help` 和 `.venv\Scripts\python.exe packaging/build_macos.py --help` 都能显示新参数。
4. 汇报里列出：改了哪些函数；按契约逐条说明是怎么实现的；单独写一节“假设与疑问”，列出规格里你觉得有歧义、自己做了判断的地方。
