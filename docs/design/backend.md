# 后端设计

本文记录后端分层、数据和外部服务方面的约定，以及这些约定的原因。目录结构见[开发 · 项目结构](../zh-CN/development.md#项目结构)，HTTP 接口见 [api.md](api.md)。

## 分层

一次页面请求：`app.py` 路由 → `queries/` 读库 → `viewmodels/` 整理成页面数据 → Jinja 渲染。

| 层 | 放什么 | 不放什么 |
|---|---|---|
| `app.py` | 参数解析、调用、选择模板或 JSON、错误转状态码 | SQL、页面数据拼装、业务流程 |
| `queries/` | 只读 SQL，返回原始行 | 写操作、展示文案 |
| `models/` | 写操作和本机状态表 | 页面逻辑 |
| `viewmodels/` | 卡片、徽章、进度、文案等展示形状 | 数据库访问 |
| `services/` | 业务流程：录入、关联任务、识别、播放、设置 | 直接拼 HTTP 响应 |
| `connectors/` | 对外部系统的一次调用：TMDB、评分、网盘、ffprobe、播放器、LLM | 业务判断 |

这样分的原因：只读快照只需要 `queries/` + `viewmodels/` + 模板，不能牵连写操作和外部调用。

## 数据库

| 库 | 内容 | 管理位置 | 进入云端快照 |
|---|---|---|---|
| `library.db` | 媒体展示数据：作品、季集关系、图片、演员、文件、播放进度、收藏 | 结构 `migrations/library_schema.sql`，写入 `models/library.py` | 是 |
| `local.db` | 本机状态：扫描、识别缓存、关联任务、网盘配置与凭据、API 用量、评分缓存 | `models/local_store.py`、`drive_store.py`、`import_store.py`、`api_usage.py` | 否 |
| `imdb/index.sqlite3` | 可重建的 IMDb 本地库：官方单集表（四整数列、复合主键）与评分表，均 WITHOUT ROWID，附来源版本 | `models/imdb_episodes.py` | 否 |

- 凭据、令牌和只对本机有意义的状态放 `local.db`，因为 `library.db` 会被同步到云端。
- 改 `library.db` 结构时同时改两处：`library_schema.sql`（新库）和 `models/library.py` 的 `upgrade_library`（老库补列）。只读快照不执行升级，所以新列在模板里要能处理缺失。
- 单集直接保存季号和集号（`title_relation`），没有 season 实体；IMDb ID 是 `title_id` 主键，没有 IMDb ID 的单集不录入。
- 单集 ID 按库内已有关系 → TMDB 外链 → 本地 IMDb 单集表回退；IMDb 表仅补 TMDB 季集目录内仍缺失、候选唯一且 ID 不冲突的位置。整剧 IMDb ID 未知、TMDB 不含该集、文件集号不明仍需核对。
- `media_file.alist_path` 是历史命名，实际存的是 MovieDB 内部媒体路径（第一级目录是挂载名），与 AList 无关；接口里的 `alist_url` 等字段也一样。

### SQL 书写

按 [SQL 样式指南](https://www.sqlstyle.guide/zh/)：

- 关键字大写；表名、列名用小写加下划线；表别名用有意义的词（`title`、`file`、`relation`），不用 `t1`、`a`。
- `SELECT` / `FROM` / `JOIN` / `WHERE` / `ORDER BY` 右对齐成一条“河道”，列表每行一项，参考 `queries/home.py`。
- 值一律用参数（`?` 或 `:name`）传入。f-string 只用于拼接代码里的固定 SQL 片段（如 `CARD_ARTWORK_SQL`），不拼接任何外部输入。
- 多步写入放在同一个事务里，失败时整体回滚。

## 录入规则

- 影视资料以 TMDB 为准。中文名按 zh-CN → zh-TW → zh-HK 回退；分类按 `tmdb.GENRE_NAMES` 转成 IMDb 英文分类。
- 重新录入时，库内已有的名称、分类和季集关系保持不变；新数据缺失的字段保留旧值（写入用 `COALESCE(excluded.x, 原值)`）。原因：用户可能手动改过，外部数据也可能临时缺失。
- 识别（规则、AI）只产出建议，入库统一走 `services/media_association.complete_association`。AI 结果必须再经 TMDB 校验，因为 AI 可能编造 ID。
- 多层目录先由 `services/media_grouping.py` 按作品/季/画质与文件片名分组（同一目录里年份相差超过一年的同名电影分开；紧贴中文的四位数如“请回答1988”既按片名也按年份去搜）；完整读取后，AI 可在快照内提出调整。`recognition_harness.py` 只允许快照取证、TMDB 搜索、候选详情和季集查询，模型返回的编号必须覆盖全部文件且每个只出现一次，失败保留规则分组。AI 标为不确定（review）的分组需要人工核对；但剧集的每个文件都对上该作品的单集、片名一致且没有其他问题时，分组视为已被季集核对证实（同一季按命名拆成几个画质目录只是多个版本），可以进入“可确认”；电影没有季集佐证，仍需核对。
- 识别输入保留相对路径并分层采样，附上 TMDB 候选。高规则分数不能绕过文件片名/年份/季集检查；业务校验冲突会触发有限 AI 补查。每个分组/纠错阶段最多 3 次模型调用、8 次只读工具调用，90 秒后不再启动新步骤，输入限 48000 字符；额度耗尽或证据不足保留待核对，不自动入库。取证及校验轨迹随本机识别缓存保存。
- 识别与分组缓存包括模型端点、模型名、思考参数、提示词/流程版本和文件内容签名；不保存密钥、直链或令牌。评测脚本 `scripts/evaluate_recognition.py` 可离线核对文件归属，`--live` 在独立数据库中调用现有 AI/TMDB，人工标签用于检测误合并/误拆分，不能用模型自评替代身份准确率。
- 关联任务（`services/import_jobs.py`）按根 Title ID 调度并持久化，恢复时复用已完成的探测结果。任务里只存路径、映射和探测结果；签名链接和令牌会过期，用到时再获取，不写入任务或数据库。
- 关联任务的探测失败按文件保留原因，不取消其他文件；成功文件分别提交，每个文件的多步写入仍在一个事务中。`result.rows` 保存逐文件错误与写入结果，重试先核对库内实际映射，跳过已关联文件，探测成功但写入失败的文件复用检查点；可只重试指定文件。手动 JSON 批量导入不启用部分成功，仍整批原子。
- 核心关联先提交，图片、演员、评分随后补齐；豆瓣评分走独立队列，不阻塞导入。

## 外部服务

- 所有外部请求经 `connectors/throttle.py` 的 `throttle.call(服务名, ...)`：令牌桶限速、自适应并发，遇到 429/503 自动退避或熔断。调用方不另加 sleep 或重试，否则会和 throttle 的退避叠加。
- 熔断时抛出 `RateLimitError`，调用方负责降级（例如 AI 识别退回规则识别）。
- ffprobe 的 HTTP 5XX（含 `/stream` 的 502）视为网盘暂时不可用，标记可重试并沿用关联任务的退避和三轮尝试上限，不另加重试循环。
- MDBList 有每日请求预算（`models/api_usage.py`），重试同样计入预算。
- TMDB 密钥只发给 TMDB；图片可能经第三方 CDN 下载，下载图片的请求不带密钥。
- IMDb 单集编号与评分共用一次更新、一个本地库和一个状态，保证两者同步。`connectors/imdb_episodes.py` 经统一限速器流式下载官方 `title.episode.tsv.gz` 与 `title.ratings.tsv.gz`，各自用 ETag 条件检查；只要有一个变化就两个都重新下载，因为要一起建库。`services/imdb_identity.py` 后台建立临时 SQLite，检查格式、完整性和数据量后原子发布，失败保留旧库；临时下载不长期保留。新库发布后用 `rating_sync.refresh_imdb_ratings` 同步库内评分。索引只保存编号与评分，不替代 TMDB 资料；按 IMDb 非商业数据集条款使用。
- IMDb 数据默认每周检查，可在设置改成每天或仅手动；调度只在 MovieDB 运行时执行，启动后补查，失败一小时后再自动尝试。手动更新与定时任务共用单飞任务。新索引发布后通过版本签名使识别缓存失效并触发重新扫描，不自动关联入库；304 只更新检查时间。当前桌面单进程共享读写锁，多进程部署需单一更新进程。

## 媒体来源与播放

- 播放器和 ffprobe 只访问 MovieDB 自己的 `/stream` 地址，由 `services/media_stream.py` 转发；不把网盘直链交给外部程序，因为直链会过期且可能带令牌。同目录及 `Subs`/`字幕` 等子目录下的外挂字幕在启动播放器前由 MovieDB 缓存到本机临时目录（本机挂载直接用原路径），再以本地文件路径传给播放器（避免 MPC-HC、IINA、VLC 对远程 HTTP 字幕地址的兼容问题），查找或下载失败不阻塞视频播放。
- 播放器默认用安装包自带的官方 mpv（`paths.BUNDLED_MPV`），作为独立进程启动，通过 JSON IPC 读取进度：macOS / Linux 用 Unix 套接字，Windows 用命名管道。不嵌入 libmpv，因为界面是系统 WebView，嵌入画面要另做原生窗口，浏览器模式也用不了。只有内置 mpv 加 `--config-dir` 使用 `packaging/mpv/mpv.conf`，用户自己的播放器不改动配置。官方 macOS 包要求 macOS 15，更低版本不使用内置 mpv（`player.BUNDLED_MPV_MIN_MACOS`）。
- `/stream` 的 502 与上游 5XX 记录失败阶段、异常类型及已知上游 HTTP 状态（不可得时为 unknown）。驱动可随 `DriveError` 保留状态码；不记录异常原文、堆栈、Cookie、直链或本机播放签名。
- 由哪个网盘提供路径由 `services/media_source.route` 决定：第一级目录由同名网盘提供；网盘停用后路径不可用，只有用户在网盘页选择“改由透传提供”时才交给透传（`*`）。改变路由的操作会触发重新扫描。
- 网盘驱动实现 `connectors/drives/base.py` 的 `Drive` 接口（`root` / `check` / `list` / `link` / `keepalive`）。
- 凭据保活由 `services/drive_keepalive.py` 调度：启动后执行一次，之后按驱动的 `keepalive_interval` 执行，使用 `media_source.drive_for` 里与播放共用的实例，续期结果直接对播放生效。新驱动有会过期的会话或令牌时，要实现 `keepalive()` 并设置间隔；没有凭据的驱动把间隔设为 0。夸克只在请求不带 `__puus` 时下发新值，续期见 `QuarkDrive.keepalive`。
- 从 AList 导入：`connectors/drives/alist_config.py` 只读解析 `data.db` / 备份 JSON，`services/alist_import.py` 把 AList 驱动名和字段映射到内置驱动（字段名以 AList 源码 `drivers/<驱动>/meta.go` 的 json 标签为准）。新增内置驱动时，在 `MAPPERS` 里补上对应关系。
- 登录方式由驱动声明：扫码实现 `start_login()`（返回二维码、内部状态和可选提示）/ `finish_login(state)` 并设置 `qr_app`；网页授权设置 `oauth_txt`（OpenList 授权页的驱动标识）和 `oauth_embedded`（桌面版能否在内嵌窗口完成，Google 不能）。有这类登录方式时，令牌字段标 `manual=True`，收进“手动填写凭据”。登录会话保存在 `services/drives.py` 的服务端字典里（5 分钟有效），令牌不经过页面。
- 一次性的 refresh_token（如 115）：续期后立即写回，并发请求只续期一次（比较发起时的 access_token），否则会把刚换到的新令牌作废。驱动返回的时间统一转为 UTC（`base.utc_iso`），比较版本时用 `same_instant` 按时刻比较，因为不同网盘的时区和格式不同。
- `/stream` 地址跟随实际监听端口（`media_source.remember_local_port`），不要写死端口。

## 配置与路径

- 设置页上的项以 `services/settings.py` 的 `SECTIONS` 为唯一来源：设置页按它渲染，保存时按它校验并写入 `.env`。要在设置页显示的新变量登记到 `SECTIONS`；不常改的变量（网盘速率、扫描、代理等）只在 `.env` 配置，不登记。两类都要补进 `.env.example` 和 configuration.md（测试会核对 `SECTIONS` 与 `.env.example`）。只有标记 `restart` 的项需要重启。
- 文件路径从 `services/paths.py` 取：程序文件在 `APP_DIR`，用户数据（数据库、`.env`、图片、日志）在 `DATA_DIR`。源码运行时两者相同，安装版分开，所以用 `Path(__file__)` 拼数据路径在安装版里会出错。
- 代码要在 Windows 和 macOS 上都能运行，平台差异集中在 `desktop.py`、`desktop_window.py`、`connectors/player.py`。

## 云端只读快照

`cloud/` 下是 Cloudflare Worker，本机离线时提供只读页面，复用同一套模板。`MOVIEDB_READ_ONLY=1` 时只放行 `app.py` 中 `READ_ONLY_ENDPOINTS` 列出的 GET 端点，其余请求返回 503。新增可以只读浏览的页面时要加入这个集合；写接口不需要额外处理。
