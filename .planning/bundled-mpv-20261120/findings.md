# 内置 mpv：调研记录（2026-11-20）

## 现有播放链路

- `connectors/player.py` 已支持 MPC-HC（Web 接口轮询）、mpv / IINA（JSON IPC）、VLC、系统默认程序。
- Windows 上 mpv 不记录进度：`play_video` 里 `ipc = None if IS_WINDOWS else ...`（player.py:305），`_MpvClient` 只走 Unix 套接字。所以 Windows 自动选择把 MPC-HC 排第一。
- MPC-HC 需要用户手动开启“Web 界面”，设置向导专门检测这一项（`services/setup_wizard.py` 的 `test_player`）。
- 播放器只访问本机 `/stream`；外挂字幕先缓存到本机，再以本地路径传给播放器（backend.md“媒体来源与播放”）。
- 平台差异集中在 `desktop.py`、`desktop_window.py`、`connectors/player.py`。

## 本机片库（782 个已探测文件）

| 项目 | 分布 |
|---|---|
| 容器 | MKV 629，MP4 153 |
| 视频编码 | HEVC 617，H.264 165 |
| 动态范围 | 杜比视界 151，HDR10 92，SDR 539 |
| 像素格式 | 10 bit 358，8 bit 424 |
| 音频 | EAC3 430，AAC 178，DTS 89，TrueHD 42，AC3 34，FLAC 9 |

结论：约 31% 是 HDR，其中多数是杜比视界，必须走 `gpu-next`（libplacebo），所以不能精简掉着色器编译链；用完整的官方包更省事。

## 发布包体积（v0.8.0）

| 包 | 大小 |
|---|---|
| MovieDB-Setup-0.8.0.exe | 14.8 MB |
| MovieDB-0.8.0-portable.zip | 18.9 MB |
| macOS dmg arm64 / x86_64 | 22.1 / 22.6 MB |

## mpv 官方发布 v0.42.0（GitHub mpv-player/mpv，压缩包大小）

| 资产 | 大小 | 备注 |
|---|---|---|
| mpv-v0.42.0-x86_64-pc-windows-msvc.zip | 27 MB | 最小的 Windows x64 包，拟采用 |
| mpv-v0.42.0-x86_64-w64-mingw32-full.zip | 62 MB | |
| mpv-v0.42.0-macos-15-arm.zip | 46 MB | 拟采用；文件名表明按 macOS 15 构建 |
| mpv-v0.42.0-macos-15-intel.zip | 50 MB | 拟采用 |
| libmpv-v0.42.0-x86_64-w64-mingw32-gpl.zip | 62 MB | 嵌入方案才用，本次不用 |

第三方 Windows 构建（shinchiro / zhongfly）的 7z 包约 31 MB，用官方包可以少一个来源。

## 阶段 0 实测（2026-11-20，Windows 11 本机）

### 官方包

| 包 | 字节数 | SHA-256 |
|---|---|---|
| mpv-v0.42.0-x86_64-pc-windows-msvc.zip | 28,696,222 | `8dd69cbab079086765fa5d873c09513b2834b85d146c1301718a757e8c0595ef` |
| mpv-v0.42.0-macos-15-arm.zip | 48,835,385 | `be1b8d2445e6880860869c080b8710dee189721f1fa4689840db6a2c10d7be69` |
| mpv-v0.42.0-macos-15-intel.zip | 53,343,668 | `2d2f34350ff844b6014c35f21ca142937275e19c0c673150a8564fcd2f3bfa14` |

- **Windows zip** 里只有 5 个文件，都在根目录：`mpv.exe`（63.0 MB，静态链接）、`vulkan-1.dll`（0.54 MB）、`mpv.com`（10 KB，控制台包装）、`mpv-register.bat`、`mpv-unregister.bat`。**只需要带前两个**，解压后共 63.5 MB。zip 里没有许可证文件。
- **macOS zip** 里只有一个 `mpv.tar.gz`（用 tar 保留权限和签名扩展属性），解开后是 `mpv.app/`：
  - arm 版 110.2 MB，intel 版 129.5 MB；
  - `Contents/MacOS/mpv` 加上 `Contents/MacOS/lib/` 下约 113 个 Homebrew 动态库，另有 `Frameworks/libMoltenVK.dylib` 和 `Resources/vulkan/icd.d/`；
  - 没有符号链接。最大的几个文件：libavcodec 11–13 MB、libx265 7–17 MB、libshaderc 7.5 MB、MoltenVK 5 MB、libcrypto 5 MB。编码器（x265、SvtAv1Enc、rav1e、aom）和 OCR（tesseract）都是 libavcodec / libavfilter 的链接依赖，**删掉就无法加载，不能裁剪**。
- **macOS 最低版本**：`Info.plist` 没有写 `LSMinimumSystemVersion`。解析 Mach-O 的 `LC_BUILD_VERSION`：`mpv` 主程序在两个架构上都是 15.0，绝大多数库是 15.0（intel 版多数是 14.0）；arm 版的 `libtesseract.5.dylib` 是 **15.8**。dyld 一般不会因为 minos 拒绝加载，但 15.0–15.7 上实际能否运行，要在 Mac 上验证。阈值先定 `(15, 0)`。
- 许可证：已从 mpv 仓库 v0.42.0 取回 `LICENSE.GPL` 和 `Copyright`，放在 `packaging/mpv/`，打包时一起复制。

### Windows 命名管道 IPC（真实 mpv 0.42.0）

- `--input-ipc-server=\\.\pipe\<名字>` 和只写 `<名字>` 都可以，mpv 会自动补上 `\\.\pipe\` 前缀。
- Python 用 `open(r"\\.\pipe\<名字>", "r+b", buffering=0)` 可以直接读写：mpv 启动约 0.1 秒后能连上，`get_property mpv-version` 正常返回，没有文件时 `time-pos` 返回 `property unavailable`。
- 发送 `quit` 后，先收到一条 `request_id: 0` 的回复，再读就是 EOF（`read` 返回空），可以据此判断 mpv 已退出。
- 注意：Git Bash 会把命令行里的 `\\.\` 改坏，在 Bash 里手动测试时要用 Python 的 `subprocess` 启动 mpv。

### mpv.conf

- `packaging/mpv/mpv.conf` 已经用 `mpv.com --config-dir=<目录>` 加载过，5 个选项都生效，没有报错。
- `target-colorspace-hint` 在 v0.42.0 里的取值是 `auto|no|yes`，默认 `auto`，所以不用写。
- 杜比视界、HDR10、TrueHD 的实际播放效果，留到验收时用真实片源测。

### 体积（用 Python lzma preset 9 估算，近似 NSIS 的 solid LZMA 和 dmg 的 ULMO）

| 部分 | 解压后 | LZMA 压缩后 |
|---|---|---|
| Windows：mpv.exe + vulkan-1.dll | 63.5 MB | 20.1 MB |
| macOS arm：mpv.app | 110.2 MB | 31.6 MB |
| macOS intel：mpv.app | 129.5 MB | 36.6 MB |

据此预估：Windows 安装包约 35 MB（原来 14.8 MB）；免安装 zip 用的是 deflate，约 47 MB；dmg arm 版约 54 MB、intel 版约 59 MB。安装后占用：Windows 多出约 64 MB，macOS 多出约 110–130 MB。
