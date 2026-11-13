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

**macOS**：`brew install uv ffmpeg`，再安装一个播放器（推荐 [IINA](https://iina.io)：`brew install --cask iina`，或 `brew install mpv`），之后与 Windows 相同。内置网盘驱动不需要 AList；使用 AList / OpenList 透传时需自行运行 macOS 版服务。

## 项目结构

| 路径 | 内容 |
|---|---|
| `app.py` | Flask 路由 |
| `desktop.py` | 桌面入口：启动服务、托盘 / 菜单栏、单实例、重启 |
| `desktop_window.py` | 原生窗口（pywebview）：关窗隐藏、记住位置、深浅色标题栏、外链与文件选择框、macOS 菜单 |
| `queries/` | 只读查询（页面查询只读 `library.db`） |
| `models/` | 写操作：`library.py`（媒体库）、`local_store.py` / `drive_store.py`（`local.db`） |
| `viewmodels/` | 页面 ViewModel |
| `services/` | 导入、关联、识别、播放、删除、设置、数据目录等业务；`media_source.py` 统一挂载解析，`media_stream.py` 负责 `/stream` 转发 |
| `connectors/` | 外部接口：TMDB、MDBList、IMDb 数据集、AI、ffprobe、播放器、限速；`drives/` 为内置网盘与 AList / OpenList 透传驱动 |
| `templates/`、`static/` | Jinja 模板、样式与按页拆分的脚本；第三方库在 `static/vendor/` |
| `migrations/` | 数据库结构，`library_schema.sql` 为当前结构 |
| `scripts/` | 命令行工具：缩略图、评分、快照发布、`.env` 核对等 |
| `packaging/` | Windows / macOS 安装包与精简版 ffprobe 的构建脚本 |
| `cloud/` | 云端只读快照的 Cloudflare Workers |

代码约定见 [AGENTS.MD](../../AGENTS.MD)；设计细节见 [前端设计](../design/frontend.md)、[后端设计](../design/backend.md)、[HTTP 接口](../design/api.md)。

## 测试

```sh
uv run --with pytest python -m pytest -q
```

更新代码后可运行 `uv run python scripts/check_env.py` 核对 `.env` 与 `.env.example` 的变量名。

## 离线测试接口

本地回放服务器只提供《绝命毒师》（IMDb `tt0903747` / TMDb `1396`）的中文 TMDb 数据和 MDBList 评分，不请求远程 API。搜索支持简繁中文、英文及部分名称；支持详情、`append_to_response`、季集资料、IMDb 映射，以及当前搜索和导入用到的图片；未收录的内容返回空结果或 404。

快照位于 `local-test-data/breaking-bad/`（被 Git 忽略），独立于业务库和 `static/images`，删除网站里的作品不会删除快照。其中包含真实 API 响应、相关的业务库记录、剧照 / 海报 / 背景 / 标志 / 演员照片，以及图片 URL 映射和 SHA-256 清单；单集 find 响应由保存的季资料与单集 external_ids 组合，旧快照没有译名时按“无译名”回放。不复制网盘视频。

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
- 网盘列目录和播放照常运行；IMDb 数据仍从官方地址下载。
- 环境变量要设置在实际启动 MovieDB 的进程中，并重启应用以清空旧缓存；只编辑 `.env` 不会替换 TMDb 地址。恢复远程服务时移除这三个变量并重启。

重新抓取：`uv run python scripts/capture_breaking_bad.py --output local-test-data/breaking-bad-new`，再用 `--data local-test-data/breaking-bad-new` 启动回放服务器。抓取会访问远程 API，但不写业务库；已有快照目录禁止覆盖。

### 后台任务圆钮演示

改动 `task-center.js` 或它的样式后，可以不提交真实任务，直接在演示页里看各种状态：

```sh
uv run python scripts/task_center_demo.py
```

打开 http://127.0.0.1:5070 ，页面上可以切换场景（单个关联、5 个关联排队依次完成、关联 + 扫描 + 评分、失败与需处理、等待接口恢复），点选海报模拟“确认关联”的飞入动画，或触发“实时活动”胶囊。演示页用的是真实的模板、样式和脚本，后台任务接口是按时间推进的模拟数据；片名和海报只从本机媒体库读取，不写任何数据。窗口窄于 576px 时任务卡片铺满屏宽，也可以打开 `/device?w=390&h=844&scenario=multi` 用固定尺寸的框模拟手机。

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

`.github/workflows/tests.yml` 在 PR 上运行 Linux、Windows、macOS 的 Python 测试、前端规则测试与关联脚本语法检查，不连接真实网盘或评分服务。安装包工作流复用同一测试矩阵，全部通过后才构建 Windows / macOS 安装包；`models/`、查询、迁移和环境配置变更也会触发构建。

`.github/workflows/windows-package.yml`（“安装包（Windows / macOS）”）在推送到 `main`、`release/**`、`feat/**`、`perf/**` 且改动了程序文件时运行：编译并校验精简版 ffprobe，构建 Windows 安装包与免安装 zip、两种架构的 macOS .dmg，并测试安装、免安装版数据位置、旧版数据迁移与 .dmg 签名。各分支的构建产物在运行记录底部的 Artifacts 下载（需登录 GitHub）。

推送到 `main` 时，如果还没有 `v<pyproject.toml 版本号>` 的 Release，就自动创建并上传安装包，发行说明取 [CHANGELOG.md](../../CHANGELOG.md) 中 `## v<版本号>` 的段落。

平时不需要逐次更新 CHANGELOG，发版时根据提交历史一次汇总。发布新版本：

1. 先合并最新的 `origin/main`，确认工作区干净。
2. 运行 `git log v<上一版本>..HEAD --no-merges --format="%h %s%n%b"`，把用户能感知的改动按主题（播放、网盘与导入、界面、安全等）写进 CHANGELOG 的“未发布”。每条说清改了什么、对用户有什么影响；纯重构、测试和开发文档的改动不写。
3. 对照这些改动检查 README 和 `docs/`：搜索涉及的功能、设置项和界面名称，更新过时的描述，并确认中英文档逐段对应。
4. 把“未发布”改为 `## v<新版本>`，在上方留一个空的“未发布”；修改 `pyproject.toml` 的版本号，运行 `uv lock` 同步 `uv.lock`。新功能升次版本号，只有修复时升修订号。
5. 运行测试后合并到 `main`，CI 会自动创建 Release。

在 Claude Code 中可以用 `/release` 按以上步骤执行。

## 前端约定

- 首页顶部为“最新入库”精选轮播（Embla），背景缓慢推镜，页面环境色随当前影片变化。
- 海报 / 背景图先显示 BlurHash 模糊占位，再淡入原图；色调表 `static/thumbnails/tones.json` 在录入时生成，启动时自动为旧图片补齐（`MOVIEDB_TONES_DISABLED=1` 可跳过）。
- 桌面端海报悬停倾斜、列表进入视口时依次浮现、卡片到详情页的共享元素转场（View Transitions，Chrome / Edge / Safari 18+）。系统开启“减少动态效果”时自动关闭。
- 第三方库（Embla Carousel、Motion、vanilla-tilt、BlurHash，均为 MIT）随仓库放在 `static/vendor/`，不走 CDN。
- 资源管理页与设置页使用统一的底部状态栏（`static/status-bar.js`）：固定在视口底部居中，页面底部自动留出空间，下拉菜单显示在状态栏之上。状态栏只放当前视图的操作；后台任务（关联、扫描、刷新评分、IMDb 数据更新）交给 `static/task-center.js`（见下一条）。预设有 `selection`（已选 N 项 + 批量操作）、`check`（校验结果 + 提交）、`progress`（确定 / 不确定进度）、`result`（成功 / 警告 / 失败，可自动收起，悬停时暂停）、`notice`（建议操作）和 `pending`（未保存的修改：放弃 / 保存），按 key 调用 `StatusBar.<预设>(key, …)` 更新、`StatusBar.clear(key)` 收起。状态栏外圆角 = 内部控件圆角 + 内边距 + 边框（控件 2rem 胶囊、内边距 0.5rem），内外弧线同心。
- 后台任务中心（`static/task-center.js`）：
  - 平时收成左上角菜单按钮旁的小圆钮，所有页面都固定在这个位置，只在有任务时出现。排队的任务叠成一串圆形海报（最多 3 张，多出的显示“+N”），正在处理的在最右、外圈是它的进度环；它完成后向右滑出，后面的依次往右挪一格接上。有失败或需处理的任务时右上角有提醒点。圆钮和圆形按钮要写 `corner-shape: round`，否则会继承全站按钮的超椭圆圆角，变成圆角方形。
  - 提交、完成、失败时，圆钮横向展开成胶囊显示这件事，几秒后缩回。
  - 点圆钮，任务在圆钮下方排成一列胶囊卡片（没有外层面板，手机上铺满屏宽）：左边是圆角海报，右边是名称、状态和进度。卡片和海报按 G2 圆角做成超椭圆。点卡片原地展开当前进度：关联分四步（核对资料 → 获取链接 → 读取参数 → 写入媒体库），总进度 =（已完成的步数 + 当前这步的完成比例）÷ 4，“获取链接”和“读取参数”按文件数计；出错时才列出错误内容和重试 / 忽略，进行中可以停止。写入媒体库就算完成，之后补海报、评分和预览图不再占着列表。
  - 成功结束的任务显示“完成”后向右滑出并自动清除，失败或需处理的任务留着，直到重试或忽略。
  - 关联页确认后调用 `TaskCenter.arrive(groups, sources)`，海报沿曲线飞进左上角的圆钮；底部的确认栏只管当前操作，不显示后台任务。
  - 换页时先用 sessionStorage 里缓存的状态立即渲染，圆钮在新页面直接出现在原位。圆钮不单独设 `view-transition-name`：它在每页的位置相同，跟着整页一起过渡即可，单独截图过渡反而可能出现方形空白。
  - 只读快照没有任务中心，任务才显示在状态栏上层。
- 资源管理里的弹窗（选择作品、扫描目录、删除作品）统一居中、淡入并轻微放大；选择作品弹窗高度固定，从待关联打开时自动按预填名称搜索。概览卡片试用图片后，卡片标题旁显示“未保存”，底部状态栏提供“放弃 / 保存”（多部作品可一次保存），离开页面前会提醒。
- 圆角与曲率（G2）的统一规则、适用范围和例外见[前端设计](../design/frontend.md#圆角与曲率g2)。
