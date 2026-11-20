# 内置 mpv 播放器（方案 A）设计草案

状态：草案（2026-11-20），末尾的“待确认”解决后再派活。调研数据见 [findings.md](findings.md)。

## 目标

- 安装包自带 mpv。Windows 和 macOS 装好就能播放并记录观看进度，不用再自己装 MPC-HC、开 Web 接口。
- 两个平台用同一版 mpv、同一份配置，体验一致：10 bit HEVC、HDR10、杜比视界、TrueHD / DTS 都能正确播放。
- MPC-HC、IINA、VLC、自定义播放器保留为可选项，原来的行为不变。

## 不做

- 不嵌入 libmpv，也不在 MovieDB 窗口里播放（以后要做再另立项）。
- 不自己编译 mpv，直接用官方发布包。
- 不做首次播放时再下载（方案 C）。
- 第一阶段不做：复用同一个 mpv 窗口、音频直通开关、窗口标题显示片名、用户自定义 mpv 配置。这些列在“第二阶段”。

## 决定

1. **来源**：mpv 官方 GitHub Release，固定 v0.42.0。版本号、下载地址和 SHA-256 写死在打包脚本里，校验不通过就停止打包。
   - Windows x64：`mpv-v0.42.0-x86_64-pc-windows-msvc.zip`（27 MB）
   - macOS arm64 / x86_64：`mpv-v0.42.0-macos-15-arm.zip` / `mpv-v0.42.0-macos-15-intel.zip`（46 / 50 MB）
2. **运行方式**：外部进程加 JSON IPC，沿用现有的 `_MpvClient`。Windows 补上命名管道，两个平台都能记录进度。
3. **安装位置**（和 ffprobe 一样放在 `runtime/` 下）：
   - Windows：`runtime/mpv/mpv.exe`
   - macOS：`Contents/Resources/runtime/mpv/mpv.app`，可执行文件在 `mpv.app/Contents/MacOS/mpv`
   - 配置：`runtime/mpv/config/mpv.conf`，打包时从仓库的 `packaging/mpv/mpv.conf` 复制过去
4. **选择顺序**：
   - 自动选择时，有内置 mpv 就用内置 mpv；没有时（源码运行、或安装时没勾选）按原来的顺序。
   - 选了“mpv”又没填路径时，先找内置的，再找系统里装的。
   - 填了 `PLAYER_PATH` 时以填写的为准，和现在一样。
5. **配置**：只有启动内置 mpv 时才加 `--config-dir=<runtime/mpv/config>`。用户自己的 mpv 继续用用户自己的配置。
6. **安装包**：
   - NSIS 增加可以取消勾选的组件“内置 mpv 播放器”，做法和 ffprobe 组件一样。
   - `VERSION.txt` 记录 mpv 版本。
   - 附上 mpv 的许可证和对应版本的源码地址（GPLv2+，作为独立程序随包分发）。
7. **体积**（阶段 0 用 LZMA 估算，见 findings.md）：

   | 包 | 现在 | 预估 |
   |---|---|---|
   | Windows 安装包 | 14.8 MB | 约 35 MB |
   | Windows 免安装 zip | 18.9 MB | 约 47 MB |
   | macOS dmg arm / intel | 约 22 MB | 约 54 / 59 MB |

   不改变方案 A 的前提下，能做的瘦身：选用官方最小的 msvc 包；只解压白名单里的文件，不要 `mpv.com`、文档、安装和更新脚本；NSIS 用 solid LZMA，dmg 用 ULMO（现在已经是这样）。

## mpv.conf（已定稿：`packaging/mpv/mpv.conf`，已用真实 mpv 加载验证）

```ini
# MovieDB 内置 mpv 的配置；用户自己安装的 mpv 不使用这份配置
vo=gpu-next
hwdec=auto-safe
# target-colorspace-hint 默认 auto：显示器开了 HDR 就输出 HDR，不用写
# 进度由 MovieDB 记录
save-position-on-quit=no
keep-open=no
slang=chi,zho,zh,chs,sc,zh-Hans,zh-CN
```

## 接口契约

### `services/paths.py`

```python
BUNDLED_MPV: Path         # Windows: RUNTIME_DIR/"mpv"/"mpv.exe"
                          # 其他：  RUNTIME_DIR/"mpv"/"mpv.app"/"Contents"/"MacOS"/"mpv"
BUNDLED_MPV_CONFIG: Path  # RUNTIME_DIR/"mpv"/"config"
```

### `connectors/player.py`

- `bundled_mpv() -> str | None`：`BUNDLED_MPV` 存在、并且当前平台能运行时，返回它的路径，否则返回 `None`。macOS 低于最低版本时返回 `None`（最低版本见“待确认 1”）。
- `resolve_player`：
  - 自动选择、且没填 `PLAYER_PATH` 时，先看 `bundled_mpv()`，有就返回 `("mpv", 路径)`；
  - 选了 `mpv`、且没填 `PLAYER_PATH` 时，候选列表最前面是 `bundled_mpv()`；
  - 其他分支不变。
- `is_bundled(path: str | None) -> bool`：给设置向导用，判断路径是不是内置 mpv。
- `play_video`，`kind == "mpv"` 时：
  - 所有平台都开 IPC。Windows 用命名管道 `\\.\pipe\moviedb-mpv-<pid>-<generation>`，其他平台沿用 `_ipc_path`。
  - 路径是内置 mpv、且 `BUNDLED_MPV_CONFIG` 目录存在时，参数里加 `--config-dir=<该目录>`。
  - 参数顺序、`--start`、`--sub-file`、`--force-window=immediate` 保持现在的写法。
- `_MpvClient`：
  - Windows 用标准库打开命名管道（例如 `open(path, "r+b", buffering=0)`）。管道还没建好时会抛 `FileNotFoundError` / `OSError`，交给 `_monitor_mpv_playback` 现有的重试和截止时间处理。
  - `get()` / `close()` 的语义不变。
  - mpv 退出后读写抛 `OSError` 或读到 EOF，监控线程结束。不能卡死不返回（监控线程是守护线程，但不能因为一直阻塞而占住生成号）。
- `_monitor_mpv_playback`：Windows 上不要对管道路径调用 `unlink`。
- IINA、MPC-HC、VLC、custom、system 分支不改。

### 打包（`packaging/build_windows.py`、`packaging/build_macos.py`）

- 常量：`MPV_VERSION = "0.42.0"`，以及各平台的 URL 和 SHA-256（已实测，见 findings.md 和 w2-package.md）。
- Windows `install_mpv(target: Path, cache: Path, archive: Path | None = None) -> str`：
  - 下载（复用 `download()` 和缓存），或者用传入的 `archive`；
  - 校验 SHA-256，不一致就 `sys.exit`，提示里写出期望值和实际值；
  - 只解压白名单里的文件（`mpv.exe`、`vulkan-1.dll`），写 `SOURCE.txt`（版本、下载地址、源码地址 `https://github.com/mpv-player/mpv/tree/v0.42.0`、许可证），复制 `packaging/mpv/mpv.conf` 到 `config/`，复制 `packaging/mpv/LICENSE.GPL` 和 `Copyright`；
  - 返回 `"0.42.0"`。
- 命令行参数：`--no-mpv`（不内置），`--mpv-zip <路径>`（用本地包，CI 缓存和离线时用）。
- `VERSION.txt` 增加 `mpv <版本或“未内置”>`；体积日志和 `GITHUB_STEP_SUMMARY` 增加 mpv 一行；没内置时传 `-DNO_MPV` 给 makensis。
- macOS `install_mpv(target, archive, arch)`：
  - 官方 zip 里只有一个 `mpv.tar.gz`：先用 `zipfile` 取出它，再用 `tarfile` 的 `filter="data"` 解开（保留可执行权限、挡住路径穿越，非 macOS 上也能组装）；
  - 校验 SHA-256；按架构选包；
  - `sign()` 改成由内向外签名：先签 `mpv.app` 里的二进制，再签 `mpv.app` 整体，最后签 MovieDB.app，并保证 `codesign --verify --deep --strict` 通过。

## 分工

| 部分 | 谁做 | 文件 |
|---|---|---|
| 阶段 0：下载官方包，记录 SHA-256、文件清单、解压大小；在 Windows 上实测 mpv.conf | Claude | findings.md、`packaging/mpv/mpv.conf` |
| W1：播放器运行时 | **Neon** | `services/paths.py`、`connectors/player.py`、`tests/test_player.py` |
| W2：打包脚本 | **Neon** | `packaging/build_windows.py`、`packaging/build_macos.py`、`tests/test_packaging_mpv.py`（新建） |
| 设置页和向导的文案（显示“随安装包提供”，改 MPC-HC 相关提示和占位符） | Claude | `services/settings.py`、`services/setup_wizard.py`、`.env.example` |
| NSIS 组件、CI（缓存 mpv 包、冒烟测试） | Claude | `packaging/moviedb.nsi`、`.github/workflows/windows-package.yml` |
| 文档（中英同步） | Claude | `docs/design/backend.md`、`docs/zh-CN|en/configuration.md`、`docs/zh-CN|en/development.md` |
| 验收、真机联调、提交、开 PR | Claude | — |

W1 和 W2 改的文件不重叠，可以开两个工作树同时跑：

```bash
git worktree add ../2026-08-26-movieDB-mpv-player  -b feat/mpv-player
git worktree add ../2026-08-26-movieDB-mpv-package -b feat/mpv-package
AGY_WORKER_MODEL=gemini-4-neon-xhigh ai-worker ../2026-08-26-movieDB-mpv-player  @<W1 任务文件>
AGY_WORKER_MODEL=gemini-4-neon-xhigh ai-worker ../2026-08-26-movieDB-mpv-package @<W2 任务文件>
```

关于 Neon：

- `agy models` 里已经有 `gemini-4-neon-{low,medium,high,xhigh}`，ai-worker 通过 `AGY_WORKER_MODEL` 就能用，不需要改脚本。
- 额度检查对 Neon 是否生效还没确认，第一次派活时留意 ai-worker 的输出。
- worker 的沙箱不联网，W2 不能真的下载 mpv。所以它的测试一律用临时造的假 zip，真实下载和打包由 Claude 跑。
- 任务完成后，把 Neon 的表现（耗时、token、验收结果）补进 ai-collab 的 engines.md。

## worker 测试要求

W1（`tests/test_player.py`）：

1. 有内置 mpv 时，自动选择返回内置 mpv；没有时退回原来的顺序（Windows：mpc-hc → mpv → vlc）。
2. 填了 `PLAYER_PATH` 时，自动选择和选“mpv”都不会被内置 mpv 抢走。
3. 内置 mpv 的启动参数包含 `--config-dir`，用户自己的 mpv 不包含。
4. Windows 启动参数包含 `--input-ipc-server=\\.\pipe\moviedb-mpv-...`。
5. Windows 命名管道的真实测试：只用标准库建一个假的 mpv 管道服务端（例如 `multiprocessing.connection.Listener(family="AF_PIPE")`），回应 `get_property`，验证进度回调收到的毫秒值，以及服务端关闭后监控线程会结束。非 Windows 上跳过。
6. macOS 低于最低版本时，`bundled_mpv()` 返回 `None`（用 monkeypatch 模拟版本号）。
7. 现有的测试全部通过，不改原有断言。

W2（`tests/test_packaging_mpv.py`）：

1. SHA-256 不一致时退出，提示里带期望值和实际值。
2. 只解压白名单里的文件；zip 里有 `../` 路径的条目时拒绝（防止路径穿越）。
3. 生成 `SOURCE.txt`，复制 `mpv.conf`，返回版本号。
4. `--no-mpv` 时不调用 `install_mpv`，并且给 makensis 传 `-DNO_MPV`（mock `subprocess.run`）。
5. macOS 签名顺序：mock `subprocess.run` 后，检查 `mpv.app` 的签名调用在它的二进制之后、在 MovieDB.app 之前。

## 验收（Claude）

1. `uv run --with pytest python -m pytest -q` 全部通过；读 diff，逐条对照接口契约，特别看 worker 汇报里的“假设与疑问”。
2. 本机运行 `uv run python packaging/build_windows.py --zip`，记录安装包和 zip 的实际体积，更新 findings.md。
3. 装好安装版后真机播放：杜比视界、HDR10、10 bit HEVC、TrueHD 各一部；加载外挂字幕；关掉再打开时从上次的位置继续；设置页显示“随安装包提供”。
4. 取消勾选 mpv 组件安装时，自动选择回到原来的顺序，不报错。
5. CI 的 Windows 打包流程通过，并增加冒烟测试：`mpv.exe --version`，以及用测试小样跑一次 `--vo=null --ao=null --frames=10`。
6. macOS 没有 CI，需要你在 Mac 上跑一次 `build_macos.py` 并试播（见待确认 4）。

## 第二阶段（可选）

- 复用同一个 mpv 窗口：已有 mpv 在运行时，用 IPC 的 `loadfile` 切换文件，不再每次开新窗口；进度改用 `observe_property` 推送。
- 设置页增加“功放直通”开关，对应 `audio-spdif=ac3,eac3,dts,dts-hd,truehd`。
- 用 `--force-media-title` 在窗口标题显示片名和集数。
- 播放器选了 VLC 这类处理不好杜比视界的，播放杜比视界文件前提示。

## 待确认

1. **macOS 最低版本**（已同意按建议做）：官方 mpv 包的主程序最低要求 macOS 15.0（arm 版的 libtesseract 是 15.8，见 findings.md），MovieDB 目前最低支持 11.0。做法：macOS 15 以下自动不用内置 mpv，回到 IINA 或系统 mpv；MovieDB 本身的最低版本不变。常量 `BUNDLED_MPV_MIN_MACOS = (15, 0)`，阶段 0 实测后再调整。
2. ~~下载体积~~：**已确认（2026-11-20）仍按方案 A**，接受安装包变大。
3. ~~当前分支有未提交的改动~~（已提交 54865cc）：`tests/test_player.py`、`services/settings.py`、`services/setup_wizard.py` 等都还没提交。worker 的工作树是从提交建出来的，看不到这些改动，所以要先提交 `feat/user-feedback-opt`，再从它建工作树。
4. ~~阶段 0 要下载官方包~~（已完成）：Windows 27 MB，macOS 46 + 50 MB，来自 github.com/mpv-player/mpv。macOS 包只能在 Mac 上实际运行验证。
