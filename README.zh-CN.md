# MovieDB

[English](README.md) | **简体中文**

为网盘里的影视资源打造的本地媒体库。MovieDB 内置只读网盘层，直接列出文件并获取播放内容，从 TMDB 补全海报、演员与简介，汇总多平台评分，自动识别杂乱的资源名（可选 AI 辅助），并用本机播放器播放、记录进度。支持 Windows 与 macOS，以桌面应用 + 网页界面的形式运行。

![首页：最新入库轮播](docs/images/home-dark.webp)

## 功能

- **媒体库**：首页“最新入库”轮播、最近观看、未观看与高分推荐；按类别、年份、评分、语言筛选；收藏夹与自定义片单；浅色 / 深色主题。
- **详情页**：TMDB 剧照、片名 Logo、演员与简介；IMDb、豆瓣、烂番茄、Metacritic、Letterboxd、Trakt 评分；由 ffprobe 读取的画质信息（4K、HDR10 / 杜比视界、编码、声道）；按季浏览单集。
- **内置网盘层**：直接支持夸克 / UC、阿里云盘 Open、百度网盘、123 云盘 Open 和本机目录，也可连接已有 AList / OpenList；只读列目录和播放，不上传或删除。
- **智能识别待关联**：扫描所选网盘目录，清洗伪装文件名、搜索 TMDB 并推测季集；拿不准的交给 AI（任意 OpenAI 兼容接口，推荐 DeepSeek）。结果分为“可确认 / 待核对 / 未识别”，一键批量入库。
- **关联界面**：逐个文件核对 TMDB 单集，用可视化的文件名规则编辑器修正季集，支持按文件夹设季号、连续编号顺延、同一集多个版本。
- **本机播放**：用 MPC-HC、IINA、mpv、VLC 或系统默认程序打开 MovieDB `/stream` 地址；支持跳转、单连接和并发分段转发，MPC-HC、IINA、mpv 记录进度并续播。
- **桌面应用**：Windows 安装包或免安装 zip，macOS 提供 Apple 芯片与 Intel 两种 .dmg；自带 Python、全部依赖和 ffprobe；界面显示在独立的原生窗口里（系统自带的 WebView2 / WKWebView，不附带浏览器内核）。
- **引导与设置**：首次使用全屏引导，设置页逐项检测连接状态。
- **远程访问（可选）**：经 Cloudflare Tunnel 远程使用；电脑离线时自动切换到 Cloudflare Workers 上的只读快照。

| | |
|---|---|
| ![电影详情页](docs/images/movie.webp) | ![剧集详情页](docs/images/series.webp) |
| 电影详情、评分与画质 | 季集浏览与续播 |
| ![类别筛选](docs/images/library.webp) | ![待关联：可确认](docs/images/discovery.webp) |
| 类别筛选 | 智能识别新文件 |
| ![关联界面与季集识别规则](docs/images/association.webp) | ![设置页](docs/images/settings.webp) |
| 用识别规则修正季集 | 设置与连接检测 |

## 准备

- **一个本机目录或网盘账号**：首次引导直接添加，不需要安装 AList。各网盘凭据获取方式见[添加媒体](docs/zh-CN/adding-media.md#网盘与凭据)。
- 海报与资料来自 TMDB：安装包已内置访问密钥，不需要自己申请（也可以在设置里换成自己的）。
- 可选：**MDBList** API Key（补充评分）、**AI** 平台的 API Key（更准确的识别）、**播放器**（Windows 推荐 MPC-HC，macOS 推荐 IINA，也可用 mpv / VLC）。

## 安装

在 [Releases](https://github.com/YUGOOD515/movie-db/releases) 下载最新版本。

### Windows

运行 `MovieDB-Setup-<版本>.exe`，按当前用户安装到 `%LOCALAPPDATA%\Programs\MovieDB`，不需要管理员权限。之后从开始菜单或桌面的“MovieDB”启动，界面在 MovieDB 自己的窗口中打开（使用 Windows 自带的 Microsoft Edge WebView2；个别没有它的系统会改用浏览器）。关闭窗口后 MovieDB 继续在后台运行（扫描、同步不中断），托盘图标提供打开 MovieDB / 打开设置 / 打开数据文件夹 / 查看日志 / 重启 MovieDB / 退出；重复打开会把已有窗口带到前面。

不想安装可以用 `MovieDB-<版本>-portable.zip`：解压后运行 `MovieDB.cmd`。免安装版把数据放在自己的文件夹里，整个文件夹可以随身带走。

### macOS

下载 `MovieDB-<版本>-macOS-arm64.dmg`（Apple 芯片）或 `-x86_64.dmg`（Intel），支持 macOS 11 及以上。打开 .dmg 后把 MovieDB 拖进“应用程序”。MovieDB 在自己的窗口中打开，留在程序坞：关闭窗口后继续在后台运行，点程序坞图标重新打开，⌘Q 退出。菜单栏的“MovieDB”菜单里有设置（⌘,）、打开数据文件夹、查看日志和重启，“前往”菜单可返回 / 前进（⌘[ / ⌘]）。

应用没有 Apple 开发者证书签名，首次打开会被拦截：到“系统设置 → 隐私与安全性”底部点“仍要打开”（只需一次），或在终端执行：

```sh
xattr -dr com.apple.quarantine /Applications/MovieDB.app
```

### 升级

直接安装新版本覆盖即可。媒体库与设置保存在独立的数据目录，升级不受影响；卸载默认只删除程序，可选择一并删除数据。

## 开始使用

MovieDB 在本机 `http://127.0.0.1:5000` 运行服务（5000 端口被其他程序占用时，例如 macOS 的“隔空播放接收器”，自动改用 5050–5059 中空闲的端口并记住），并在窗口中打开（想用浏览器可在设置 → 连接 → 本机 → 界面打开方式中改为“系统浏览器”）。首次使用进入全屏引导，右侧海报墙随进度逐级亮起：

![首次使用引导](docs/images/onboarding.webp)

1. **开场**：检查已添加的网盘、ffprobe 是否可用、将要新建的媒体库，并用内置密钥自动连接 TMDB（`api.themoviedb.org` 连不上时自动改用官方备用域名 `api.tmdb.org`）。
2. **片源 · 添加网盘**：选择本机目录或网盘类型，填写挂载名与凭据，检测成功后保存；至少添加一个网盘才能继续。TMDB 已连通时，这一步之后直接完成设置。
3. **海报与资料 · TMDB**（仅在 TMDB 连不上时出现）：填写只对 TMDB 生效的代理；海报加载不出来时可开启公共图片加速（wsrv.nl）；也可以填自己的 TMDB 密钥。
4. **完成**：自动创建空媒体库，并直接打开“添加扫描目录”。完成页列出可选增强：**AI 识别**（智谱 GLM-4.7-Flash 免费可用，也支持 DeepSeek、硅基流动、Gemini、OpenRouter、OpenAI 等）与 **MDBList** 多平台评分，都在设置页开启。

之后在侧栏“媒体资源管理 → 关联”添加网盘目录，确认识别结果即可。详见[添加媒体](docs/zh-CN/adding-media.md)。

## 配置

所有设置都可以在侧栏“管理 → 设置”中修改，分为**连接**（网盘、待关联扫描、播放器、ffprobe、云端只读快照、本机端口）、**元数据**（TMDB、MDBList、IMDb 评分）、**AI** 和**数据**。每组显示状态并可单独“检测”，不常用的项收在“高级”里。已保存的密码和密钥不会回显，留空即保持不变；挂载与网盘凭据在“管理 → 网盘”中维护。

设置写入数据目录的 `.env`，除端口（`MOVIEDB_PORT`）外都立即生效；改端口后可在设置页或托盘菜单“重启 MovieDB”。全部变量见[配置](docs/zh-CN/configuration.md)。

### 数据目录

媒体库（`library.db`）、本机状态（`local.db`，含扫描识别、关联任务、网盘配置与评分缓存）、设置（`.env`）、海报与缩略图、IMDb 评分文件和日志都保存在**数据目录**，托盘 / 菜单栏的“打开数据文件夹”可直接打开。

| 运行方式 | 数据目录 |
|---|---|
| Windows 安装版 | `%LOCALAPPDATA%\MovieDB` |
| macOS | `~/Library/Application Support/MovieDB` |
| Windows 免安装版（zip） | 解压目录中的 `app\` |
| 从源码运行 | 仓库根目录 |

设置环境变量 `MOVIEDB_DATA_DIR` 可以指定其他位置。“设置 → 数据”可以清除本机数据并回到首次使用引导。

### 播放器

| 播放器 | 平台 | 观看进度 |
|---|---|---|
| MPC-HC | Windows（默认） | 记录并续播，需要在 MPC-HC“选项 → Web 界面”中开启 Web 接口 |
| IINA | macOS（默认） | 记录并续播 |
| mpv | 全部 | 记录并续播（Windows 上只续播） |
| VLC、系统默认程序 | 全部 | 只打开，不记录 |

播放器留空时自动选择已安装的。

## 从源码运行

需要 Python 3.13+ 与 [uv](https://docs.astral.sh/uv/)。

```sh
uv sync
uv run --with pystray --with pywebview python desktop.py   # 独立窗口 + 托盘
uv run python desktop.py                  # 控制台运行，Ctrl+C 退出
uv run --with pytest python -m pytest -q  # 测试
```

macOS 上先 `brew install uv ffmpeg`，再装一个播放器，例如 `brew install --cask iina`。构建安装包、项目结构等开发说明见[开发](docs/zh-CN/development.md)。

## 文档

- [添加媒体](docs/zh-CN/adding-media.md)：智能识别、AI 识别、关联界面、手动录入、删除作品
- [配置](docs/zh-CN/configuration.md)：设置页、环境变量、播放器、外部接口限速
- [远程访问](docs/zh-CN/remote-access.md)：Cloudflare Tunnel 与云端只读快照
- [开发](docs/zh-CN/development.md)：源码运行、测试、离线测试接口、构建安装包
- [更新日志](CHANGELOG.md)

## 许可

MovieDB 自有代码采用 **GNU Affero 通用公共许可证第 3 版或更新版本**（全文见 [LICENSE](LICENSE)），第三方代码说明见 [NOTICE](NOTICE)。随附的第三方代码与资源沿用各自的许可（例如 `static/vendor/` 中的库，以及安装包内 LGPL 版的 ffprobe）。影视资料与图片来自 [TMDB](https://www.themoviedb.org)；本产品使用 TMDB API，但未获 TMDB 认可或认证。
