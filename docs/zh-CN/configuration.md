# 配置

[English](../en/configuration.md) | **简体中文**

- [设置页](#设置页)
- [清除数据](#清除数据)
- [数据目录](#数据目录)
- [环境变量](#环境变量)
- [AList 自动启动](#alist-自动启动)
- [播放器](#播放器)
- [外部接口限速](#外部接口限速)

## 设置页

![设置页](../images/settings.webp)

媒体库建好后，侧栏“管理 → 设置”（`/settings`）分为四栏：

| 分栏 | 内容 |
|---|---|
| 连接 | AList、待关联扫描、播放器、ffprobe、云端只读快照、本机端口 |
| 元数据 | TMDB、MDBList、IMDb 评分 |
| AI | 识别平台、模型、接口地址与并发等 |
| 数据 | 清除本机数据 |

每组显示状态并可单独“检测”，不常用的项收在“高级”里；修改后在底部状态栏“保存”或“放弃”。

- 已保存的密码和密钥不会回显，留空即保持不变；可选的密钥可以单独清除。关闭 AI 识别会一并清除其接口地址、模型与 API Key。
- 配置写入数据目录的 `.env`（保留原有注释与其他变量），除端口（`MOVIEDB_PORT`）与界面打开方式（`MOVIEDB_UI`）外都立即生效。修改这两项后桌面版可在设置页或托盘菜单“重启 MovieDB”一键重启，其他启动方式需要手动重启。
- 设置只能在运行 MovieDB 的电脑上修改：经 Cloudflare Tunnel 访问时只读，云端快照不提供该页面。
- 首次使用（还没有 `library.db`）时 `/setup` 显示全屏引导；媒体库建好后 `/setup` 跳转到设置页。

## 清除数据

“设置 → 数据”可以彻底删除本机数据并回到首次使用引导，可选择的内容：

- 媒体库（`library.db`，含关联、观看进度与收藏）
- 识别缓存与扫描记录（`discovery.db`）
- 海报与缩略图（`static/images`、`static/thumbnails`）
- 连接与密钥（`.env`）

默认全选，输入“清除”确认后执行，**不做备份**。开启了云端只读快照时，清除媒体库会先发布一份空快照，云端同步清空。正在扫描时不能清除；Windows 上文件被占用时会提示重启后再试。

## 数据目录

媒体库、配置、海报缓存与日志（`library.db`、`discovery.db`、`.env`、`static/images`、`static/thumbnails`、`imdb/`、`logs/`）都保存在数据目录：

| 运行方式 | 数据目录 |
|---|---|
| Windows 安装版 | `%LOCALAPPDATA%\MovieDB` |
| macOS | `~/Library/Application Support/MovieDB` |
| Windows 免安装版（zip） | 解压目录的 `app\`（有 `portable` 标记文件） |
| 从源码运行 | 仓库根目录 |

- 设置环境变量 `MOVIEDB_DATA_DIR` 可以指定其他位置（例如让源码运行与安装版共用一份数据）。
- 程序与数据分开，升级只替换程序文件。0.2 及更早的安装版把数据放在安装目录的 `app\` 下，新版首次启动时自动搬到 `%LOCALAPPDATA%\MovieDB`；旧版仍在运行、文件被占用时，这次继续用旧位置，下次启动再搬。
- 卸载时默认只删除程序文件，可选择一并删除数据。

## 环境变量

设置页覆盖了绝大多数变量，也可以直接编辑数据目录的 `.env`（变量名参考仓库中的 [`.env.example`](../../.env.example)），或在启动 MovieDB 的进程中设置环境变量。源码运行时 `.env` 由 python-dotenv 自动读取。

**基本**

| 变量 | 说明 |
|---|---|
| `MOVIEDB_PORT` | 本机端口，默认 `5000`，修改后需重启。未设置且 5000 被其他程序占用时（macOS 上通常是“隔空播放接收器”），桌面版自动改用 5050–5059 中第一个空闲端口并保存到这里 |
| `MOVIEDB_UI` | 桌面版界面打开方式：留空为独立窗口，`browser` 为系统浏览器；修改后需重启 |
| `MOVIEDB_DATA_DIR` | 数据目录，默认见[上表](#数据目录) |
| `MOVIEDB_DISCOVERY_DATABASE` | `discovery.db` 的位置，默认与 `library.db` 同目录 |
| `FLASK_SECRET_KEY` | 会话密钥，首次引导时自动生成 |
| `FLASK_PROFILING` | 设为 `1` 开启性能剖析 |

**AList**

| 变量 | 说明 |
|---|---|
| `ALIST_ORIGIN` | AList 地址，默认 `http://127.0.0.1:5244` |
| `ALIST_USERNAME`、`ALIST_PASSWORD` | AList 账号 |
| `ALIST_PATH_PASSWORD` | 目录在 AList“元信息”里设置了密码时填写 |
| `ALIST_DIR`、`ALIST_AUTOSTART` | AList 所在文件夹与是否自动启动，见[下文](#alist-自动启动) |
| `ALIST_RATE`、`ALIST_CONCURRENCY` | 每秒请求数（默认 20）与同时在途请求数（默认 4），网盘风控严格时调低 |
| `ALIST_IMPORT_ROOT` | 只允许导入这个目录下的文件；也是没有选择扫描目录时的默认目录。留空时自动查找“来自：分享” |

**元数据**

| 变量 | 说明 |
|---|---|
| `TMDB_TOKEN` | TMDB API 读访问令牌（不是 32 位的 API Key） |
| `TMDB_PROXY` | 访问 TMDB 用的 HTTP 代理（只对 TMDB 生效），如 `http://127.0.0.1:7890` |
| `TMDB_API_ORIGIN`、`TMDB_IMAGE_ORIGIN` | 反向代理或镜像地址，默认 `https://api.themoviedb.org/3`、`https://image.tmdb.org/t/p` |
| `MDBLIST_APIKEY` | MDBList API Key，补充烂番茄、Metacritic 等评分 |
| `IMDB_RATINGS_PATH` | IMDb 官方评分文件位置，默认数据目录的 `imdb/` |

**AI 识别**（OpenAI 兼容接口，详见[添加媒体 · AI 识别](adding-media.md#ai-识别)）

| 变量 | 说明 |
|---|---|
| `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` | 接口地址、密钥与模型；推荐 `https://api.deepseek.com` + `deepseek-flash` |
| `LLM_API` | `responses` 或 `chat`；默认 DeepSeek 用 `responses`，其他平台用 `chat` |
| `LLM_THINKING` | 思考模式 `none`（默认）/ `low` / `high` / `max`，仅 DeepSeek 生效 |
| `LLM_BATCH_SIZE` | 每次请求最多带几条（1–50），DeepSeek 默认 8，其他平台 20 |
| `LLM_CONCURRENCY` | 同时在途请求数（1–500），DeepSeek 默认 64，其他平台 2 |
| `LLM_RPM` | 每分钟请求上限，默认 10（DeepSeek 忽略） |

**扫描、媒体信息与播放**

| 变量 | 说明 |
|---|---|
| `MOVIEDB_AUTOSCAN` | 启动时后台扫描所选目录，默认 `1`，设为 `0` 关闭 |
| `FFPROBE_PATH` | ffprobe 路径；安装包自带精简版，需要时可换成完整版 |
| `FFPROBE_TIMEOUT` | 单个文件的超时（秒），默认 60 |
| `IMPORT_TITLE_CONCURRENCY` | 同时准备核心关联的作品数，默认 4，范围 1–16；资料补齐使用独立资源池 |
| `FFPROBE_CONCURRENCY` | 所有导入任务合计的 ffprobe 进程数，默认 3，范围 1–8 |
| `FFPROBE_PER_SOURCE` | 每个 AList 第一级目录的探测并发，默认跟随总上限 |
| `IMAGE_CONCURRENCY` | 缩略图与色调处理的共享并发数，默认 2 |
| `PLAYER` | `mpc-hc` / `iina` / `mpv` / `vlc` / `system`，留空自动选择 |
| `PLAYER_PATH` | 非 MPC-HC 播放器的程序路径，macOS 可以填 `.app` |
| `MPC_PATH`、`MPC_WEB_URL` | MPC-HC 程序路径与 Web 接口地址（默认 `http://127.0.0.1:13579`） |
| `MOVIEDB_TONES_DISABLED` | 设为 `1` 时启动不补算海报环境色与 BlurHash |

**云端只读快照**（见[远程访问](remote-access.md)）

| 变量 | 说明 |
|---|---|
| `MOVIEDB_SYNC_TOKEN` | 同步令牌，设置后自动同步 |
| `MOVIEDB_SYNC_URL` | 入口 Worker 地址 |
| `MOVIEDB_SYNC_DISABLED` | 设为 `1` 临时关闭自动同步 |

更新代码后可运行 `uv run python scripts/check_env.py` 核对 `.env` 与 `.env.example`：只比较变量名、不输出值，列出缺少、未登记、重复以及可选未设置的变量。

> TMDB 与 AList 凭据不要写入源码或提交到 Git。曾经进入 Git 历史的凭据应在对应服务中轮换。

## AList 自动启动

配置 `ALIST_DIR` 后，MovieDB 启动时先检查 `ALIST_ORIGIN`：AList 尚未运行时，在该目录执行 `alist.exe start`（macOS / Linux 为 `alist start`）并等待服务可用；已运行的实例不会重复启动。未配置 `ALIST_DIR` 或 `ALIST_AUTOSTART=0` 时需要自己启动 AList。AList 作为独立的后台进程运行，MovieDB 退出时不会停止它。

## 播放器

点“播放”时由本机播放器打开 AList 播放链接。在“设置 → 连接 → 播放器”中选择，留空时自动选择已安装的（Windows：MPC-HC → mpv → VLC；macOS：IINA → mpv → VLC）。

| 播放器 | 平台 | 观看进度 |
|---|---|---|
| MPC-HC | Windows（默认） | 记录并续播，需要在 MPC-HC“选项 → Web 界面”中开启 Web 接口 |
| IINA | macOS（默认） | 记录并续播（mpv 的 JSON IPC） |
| mpv | 全部 | 记录并续播（Windows 上只续播） |
| VLC、系统默认程序 | 全部 | 只打开，不记录 |

没有找到播放器时，页面会提示去设置页选择，不会报错。

## 外部接口限速

各平台的免费额度都有限，所有外部请求统一经 `connectors/throttle.py` 调度：每个服务一个令牌桶（平均速率）加自适应并发上限（429/503 时减半，连续成功后逐步恢复到配置值）。遇到 429/503 时按服务端给出的 `Retry-After`、`X-RateLimit-Reset` 或 Gemini 的 `retryDelay` 暂停整个服务后重试，没有给出时指数退避加抖动。需要等待的时间超过上限（多半是每日额度用尽）时不再排队，而是暂停该服务到恢复时间，期间请求直接降级。

| 服务 | 已知限制 | 默认策略 | 调整 |
| --- | --- | --- | --- |
| TMDB API | 上限随服务状态变化，保留余量 | 默认 25 次/秒，16 并发，无突发积攒；图片 CDN 另计，6 并发 | `TMDB_RATE`（最高 30） |
| AList（夸克等网盘） | AList 本身不限流，网盘驱动有风控，过快会提示“请求太频繁” | 20 次/秒，4 并发；识别“频繁”类错误后退避重试。“强制刷新”扫描时如常出现频繁提示，可调低到 5–10 | `ALIST_RATE`、`ALIST_CONCURRENCY` |
| AI（DeepSeek） | 不限每分钟次数，按账号并发限制（flash 2500、v4-pro 500），超出返回 429 | 64 并发（429 时自动减半），每批最多 8 条 | `LLM_CONCURRENCY`、`LLM_BATCH_SIZE` |
| AI（其他 OpenAI 兼容） | 免费档通常每分钟十几到几十次、每日几十到上千次 | 每分钟 10 次，2 并发，每批最多 20 条 | `LLM_RPM`、`LLM_CONCURRENCY`、`LLM_BATCH_SIZE` |
| MDBList | 免费档每日 1000 次，每 5 分钟最多 1000 次读取 | 2 次/秒，2 并发；连续 24 小时预算 900 次，成功评分缓存 6 小时；重试也计数 | `MDBLIST_DAILY_BUDGET`、`MDBLIST_RATE`（最高 2） |
| ffprobe | 每个进程都读取网盘文件头 | 所有作品共用 3 个进程，可额外限制每个来源 | `FFPROBE_CONCURRENCY`、`FFPROBE_PER_SOURCE` |

MDBList 的请求计数与缓存持久化到数据目录的 `api-usage.db`，可用 `MOVIEDB_API_USAGE_DATABASE` 指定位置。预算按账号凭据的哈希区分，不保存密钥。请按套餐设置预算，并为其他使用同一账号的客户端留出余量。排队请求会重新检查冷却状态，流式下载占用槽位直到读取结束；更改设置不会忘记正在进行的请求或服务冷却时间。

AI 额度用尽时，识别自动退回规则结果并在扫描提示中说明；这些结果的缓存签名记为“未用 AI”，额度恢复后重新扫描会自动补识别，不会把降级结果当成最终结论。
