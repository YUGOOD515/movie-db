# 开发

[English](../en/development.md) | **简体中文**

- [从源码运行](#从源码运行)
- [项目结构](#项目结构)
- [测试](#测试)
- [离线测试接口](#离线测试接口)
- [构建安装包](#构建安装包)
- [持续集成与发布](#持续集成与发布)
- [前端约定](#前端约定)

## 从源码运行

需要 Python 3.13+ 与 [uv](https://docs.astral.sh/uv/)。源码运行时数据目录就是仓库根目录（`library.db`、`.env` 等），可用 `MOVIEDB_DATA_DIR` 改到别处。

```sh
uv sync
uv run --with pystray --with pywebview python desktop.py   # 桌面模式：独立窗口 + 托盘
uv run --with pystray python desktop.py --browser          # 旧版方式：系统浏览器 + 托盘 / 菜单栏
uv run python desktop.py                  # 没有 pywebview / pystray 时退化为浏览器 + 控制台，Ctrl+C 退出
uv run flask --app app run                # 也可以直接用 Flask 开发服务器
```

`desktop.py` 支持 `--port`、`--browser`（用系统浏览器，等同 `MOVIEDB_UI=browser`）、`--no-browser`（只运行服务）、`--no-tray`、`--self-test`（打开窗口检查页面与接口后退出，CI 用）。还没有 `library.db` 时页面会进入首次使用引导；也可以照 `.env.example` 手动写 `.env`。

**macOS**：`brew install uv ffmpeg`，再安装一个播放器（推荐 [IINA](https://iina.io)：`brew install --cask iina`，或 `brew install mpv`），之后与 Windows 相同。AList 可下载 macOS 版手动运行，或把它所在的文件夹填到 `ALIST_DIR` 由 MovieDB 自动启动。

## 项目结构

| 路径 | 内容 |
|---|---|
| `app.py` | Flask 路由 |
| `desktop.py` | 桌面入口：启动服务、托盘 / 菜单栏、单实例、重启 |
| `desktop_window.py` | 原生窗口（pywebview）：关窗隐藏、记住位置、深浅色标题栏、外链与文件选择框、macOS 菜单 |
| `queries/` | 只读查询（页面查询只读 `library.db`） |
| `models/` | 写操作：`library.py`（媒体库）、`discovery_store.py`（`discovery.db`） |
| `viewmodels/` | 页面 ViewModel |
| `services/` | 导入、关联、识别、播放、删除、设置、数据目录等业务 |
| `connectors/` | 外部接口：TMDB、AList、MDBList、IMDb 评分、AI、ffprobe、播放器、限速 |
| `templates/`、`static/` | Jinja 模板、样式与按页拆分的脚本；第三方库在 `static/vendor/` |
| `migrations/` | 数据库结构，`library_schema.sql` 为当前结构 |
| `scripts/` | 命令行工具：缩略图、评分、快照发布、`.env` 核对等 |
| `packaging/` | Windows / macOS 安装包与精简版 ffprobe 的构建脚本 |
| `cloud/` | 云端只读快照的 Cloudflare Workers |
| `archive/` | 已退出运行链路的历史材料 |

代码约定见 [AGENTS.MD](../../AGENTS.MD)。

## 测试

```sh
uv run --with pytest python -m pytest -q
```

更新代码后可运行 `uv run python scripts/check_env.py` 核对 `.env` 与 `.env.example` 的变量名。

## 离线测试接口

本地回放服务器只提供《绝命毒师》（IMDb `tt0903747` / TMDb `1396`）的中文 TMDb 数据和 MDBList 评分，不请求远程 API。搜索支持简繁中文、英文及部分名称；支持详情、`append_to_response`、季集资料、IMDb 映射，以及当前搜索和导入用到的图片；未收录的内容返回空结果或 404。

快照位于 `local-test-data/breaking-bad/`（被 Git 忽略），独立于业务库和 `static/images`，删除网站里的作品不会删除快照。其中包含真实 API 响应、相关的业务库记录、剧照 / 海报 / 背景 / 标志 / 演员照片，以及图片 URL 映射和 SHA-256 清单；单集 find 响应由保存的季资料与单集 external_ids 组合，旧快照没有译名时按“无译名”回放。不复制 AList 视频。

先在一个终端运行本地 API：

```sh
uv run python scripts/local_test_api.py
```

停止原有 MovieDB 进程，再在启动 MovieDB 的终端中替换三个地址（PowerShell 示例）：

```powershell
$env:TMDB_API_ORIGIN = "http://127.0.0.1:5001/3"
$env:TMDB_IMAGE_ORIGIN = "http://127.0.0.1:5001/t/p"
$env:MDBLIST_API_URL = "http://127.0.0.1:5001/mdblist/api/"
uv run flask --app app run --host 127.0.0.1 --port 5000
```

- 原有 Token / API Key 可保留，本地服务器忽略凭据；没有凭据时可把 `TMDB_TOKEN` 和 `MDBLIST_APIKEY` 设为 `local-test`。
- AList 流程照常运行；IMDb 评分文件仍从官方地址下载。
- 环境变量要设置在实际启动 MovieDB 的进程中，并重启应用以清空旧缓存；只编辑 `.env` 不会替换 TMDb 地址。恢复远程服务时移除这三个变量并重启。

重新抓取：`uv run python scripts/capture_breaking_bad.py --output local-test-data/breaking-bad-new`，再用 `--data local-test-data/breaking-bad-new` 启动回放服务器。抓取会访问远程 API，但不写业务库；已有快照目录禁止覆盖。

## 构建安装包

### Windows

```sh
uv run python packaging/build_windows.py
```

脚本下载 Python 嵌入版、按 Windows 平台安装依赖、复制已纳入 Git 的应用文件（不含测试与开发资料），并用 `packaging/slim.py` 删掉用不到的部分（Pillow 的 AVIF / FreeType 等扩展、pywebview 的其他平台后端）。启动程序 `runtime\python\MovieDB.exe` 是换了图标与名称的 `pythonw.exe`（rcedit）。打包后用 `packaging/check_runtime.py` 检查依赖、缩图与窗口组件。有 `makensis`（NSIS 3）时生成安装包，否则生成免安装 zip（解压后运行 `MovieDB.cmd`）。在 Linux 上也能组装。

### macOS

```sh
uv run python packaging/build_macos.py [--arch arm64|x86_64] [--ffprobe-dir <目录>]
```

生成 `dist/MovieDB-<版本>-macOS-<架构>.dmg`。脚本下载 [python-build-standalone](https://github.com/astral-sh/python-build-standalone) 的 Python、按 macOS 平台安装依赖（通用二进制只保留目标架构）、预编译字节码，用 `packaging/macos/launcher.c` 编译启动器（Python 放在 `Contents/MacOS/MovieDB-python`，进程归属 MovieDB.app），做临时（ad-hoc）签名后打包 .dmg（LZMA 压缩）。Apple 芯片上两种架构都能构建。运行时不会往 .app 里写文件（签名保持有效），数据都在数据目录。

### ffprobe

两个脚本对 ffprobe 都有三种选择：

- `--ffprobe-dir <目录>`（CI 默认）：使用 `packaging/build_ffprobe.sh` 编译的**精简版**。只保留 file / http / https 协议、常见封装（mkv/webm、mp4/mov、ts/m2ts、avi、flv、wmv、mpg、ogg、rmvb）和常见音视频编码，静态单文件约 8 MB，仍为 LGPL。
  - Windows：`bash packaging/build_ffprobe.sh <FFmpeg 源码> <输出目录> win64`（Linux + mingw-w64），https 走系统自带的 Schannel。
  - macOS：目标用 `macos-arm64` / `macos-x86_64`（在 Mac 上编译），https 走系统自带的 Secure Transport。
  - CI 会用完整版 ffmpeg 生成测试小样（H.264 / HEVC 10bit HDR10 / AV1 / VP9 / MPEG-2 / MPEG-4 / WMV / RV，AAC / EAC3 / DTS / TrueHD / FLAC / Opus / PCM 等），经本地 HTTPS（支持 Range）逐字段比对精简版与完整版的读取结果（`packaging/check_ffprobe.py`）。
- 不指定：下载 BtbN 的完整 LGPL 版（约 160 MB）。
- `--no-ffprobe`：不内置，用 `FFPROBE_PATH` 指向自己的 ffprobe。

精简版不认识的少见格式只会缺少画质信息，不影响导入；需要时同样可以用 `FFPROBE_PATH` 换成完整版。

## 持续集成与发布

`.github/workflows/windows-package.yml`（“安装包（Windows / macOS）”）在推送到 `main`、`release/**`、`feat/**`、`perf/**` 且改动了程序文件时运行：编译并校验精简版 ffprobe，构建 Windows 安装包与免安装 zip、两种架构的 macOS .dmg，并测试安装、免安装版数据位置、旧版数据迁移与 .dmg 签名。各分支的构建产物在运行记录底部的 Artifacts 下载（需登录 GitHub）。

推送到 `main` 时，如果还没有 `v<pyproject.toml 版本号>` 的 Release，就自动创建并上传安装包，发行说明取 [CHANGELOG.md](../../CHANGELOG.md) 中 `## v<版本号>` 的段落。发布新版本：更新 `pyproject.toml` 的版本号，把 CHANGELOG 的“未发布”改为该版本号，合并到 `main`。

## 前端约定

- 首页顶部为“最新入库”精选轮播（Embla），背景缓慢推镜，页面环境色随当前影片变化。
- 海报 / 背景图先显示 BlurHash 模糊占位，再淡入原图；色调表 `static/thumbnails/tones.json` 在录入时生成，启动时自动为旧图片补齐（`MOVIEDB_TONES_DISABLED=1` 可跳过）。
- 桌面端海报悬停倾斜、列表进入视口时依次浮现、卡片到详情页的共享元素转场（View Transitions，Chrome / Edge / Safari 18+）。系统开启“减少动态效果”时自动关闭。
- 第三方库（Embla Carousel、Motion、vanilla-tilt、BlurHash，均为 MIT）随仓库放在 `static/vendor/`，不走 CDN。
- 媒体资源管理页使用统一的底部状态栏（`static/status-bar.js`）：固定在视口底部居中，页面底部自动留出空间，下拉菜单显示在状态栏之上。上层显示后台任务（扫描 AList、刷新评分、关联结果），下层显示当前视图的操作。预设有 `selection`（已选 N 项 + 批量操作）、`check`（校验结果 + 提交）、`progress`（确定 / 不确定进度）、`result`（成功 / 警告 / 失败，可自动收起，悬停时暂停）、`notice`（建议操作）和 `pending`（未保存的修改：放弃 / 保存），按 key 调用 `StatusBar.<预设>(key, …)` 更新、`StatusBar.clear(key)` 收起。状态栏外圆角 = 内部控件圆角 + 内边距 + 边框（控件 2rem 胶囊、内边距 0.5rem），内外弧线同心。
- 资源管理里的弹窗（选择作品、扫描目录、删除作品）统一居中、淡入并轻微放大；选择作品弹窗高度固定，从待关联打开时自动按预填名称搜索。概览卡片试用图片后，卡片标题旁显示“未保存”，底部状态栏提供“放弃 / 保存”（多部作品可一次保存），离开页面前会提醒。
- 圆角统一使用超椭圆（`corner-shape: squircle`）。子元素继承父级圆角时需一并写 `corner-shape: inherit`，并把半径减去父级边框宽度，否则悬停底色在四角会露出缝隙。
