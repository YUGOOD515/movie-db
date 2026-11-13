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
| `imdb/episodes.sqlite3` | 可重建的官方单集索引与来源版本，四整数列、复合主键、WITHOUT ROWID | `models/imdb_episodes.py` | 否 |

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
- 多层目录先由 `services/media_grouping.py` 按作品/季/画质与文件片名分组；完整读取后，AI 可在快照内提出调整。`recognition_harness.py` 只允许快照取证、TMDB 搜索、候选详情和季集查询，模型返回的编号必须覆盖全部文件且每个只出现一次，失败保留规则分组。
- 识别输入保留相对路径并分层采样，附上 TMDB 候选。高规则分数不能绕过文件片名/年份/季集检查；业务校验冲突会触发有限 AI 补查。每个分组/纠错阶段最多 3 次模型调用、8 次只读工具调用，90 秒后不再启动新步骤，输入限 48000 字符；额度耗尽或证据不足保留待核对，不自动入库。取证及校验轨迹随本机识别缓存保存。
- 识别与分组缓存包括模型端点、模型名、思考参数、提示词/流程版本和文件内容签名；不保存密钥、直链或令牌。评测脚本 `scripts/evaluate_recognition.py` 可离线核对文件归属，`--live` 在独立数据库中调用现有 AI/TMDB，人工标签用于检测误合并/误拆分，不能用模型自评替代身份准确率。
- 关联任务（`services/import_jobs.py`）按根 Title ID 调度并持久化，恢复时复用已完成的探测结果。任务里只存路径、映射和探测结果；签名链接和令牌会过期，用到时再获取，不写入任务或数据库。
- 核心关联先提交，图片、演员、评分随后补齐；豆瓣评分走独立队列，不阻塞导入。

## 外部服务

- 所有外部请求经 `connectors/throttle.py` 的 `throttle.call(服务名, ...)`：令牌桶限速、自适应并发，遇到 429/503 自动退避或熔断。调用方不另加 sleep 或重试，否则会和 throttle 的退避叠加。
- 熔断时抛出 `RateLimitError`，调用方负责降级（例如 AI 识别退回规则识别）。
- MDBList 有每日请求预算（`models/api_usage.py`），重试同样计入预算。
- TMDB 密钥只发给 TMDB；图片可能经第三方 CDN 下载，下载图片的请求不带密钥。
- `connectors/imdb_episodes.py` 经统一限速器流式下载官方 `title.episode.tsv.gz`，使用 ETag 条件检查。`services/imdb_identity.py` 后台建立临时 SQLite，检查格式、完整性和数据量后原子发布，失败保留旧库；临时下载不长期保留。索引只保存编号，不替代 TMDB 资料；按 IMDb 非商业数据集条款使用。
- 单集库默认每周检查，可在设置改成每天或仅手动；调度只在 MovieDB 运行时执行，启动后补查，失败一小时后再自动尝试。手动更新与定时任务共用单飞任务。新索引发布后通过版本签名使识别缓存失效并触发重新扫描，不自动关联入库；304 只更新检查时间。当前桌面单进程共享读写锁，多进程部署需单一更新进程。

## 媒体来源与播放

- 播放器和 ffprobe 只访问 MovieDB 自己的 `/stream` 地址，由 `services/media_stream.py` 转发；不把网盘直链交给外部程序，因为直链会过期且可能带令牌。
- 由哪个网盘提供路径由 `services/media_source.route` 决定：第一级目录由同名网盘提供；网盘停用后路径不可用，只有用户在网盘页选择“改由透传提供”时才交给透传（`*`）。改变路由的操作会触发重新扫描。
- 网盘驱动实现 `connectors/drives/base.py` 的 `Drive` 接口（`root` / `check` / `list` / `link`）。驱动返回的时间统一转为 UTC（`base.utc_iso`），比较版本时用 `same_instant` 按时刻比较，因为不同网盘的时区和格式不同。
- `/stream` 地址跟随实际监听端口（`media_source.remember_local_port`），不要写死端口。

## 配置与路径

- 可配置项以 `services/settings.py` 的 `SECTIONS` 为唯一来源：设置页按它渲染，保存时按它校验并写入 `.env`。新增环境变量要登记到 `SECTIONS` 并补进 `.env.example`（测试会核对两者）。只有标记 `restart` 的项需要重启。
- 文件路径从 `services/paths.py` 取：程序文件在 `APP_DIR`，用户数据（数据库、`.env`、图片、日志）在 `DATA_DIR`。源码运行时两者相同，安装版分开，所以用 `Path(__file__)` 拼数据路径在安装版里会出错。
- 代码要在 Windows 和 macOS 上都能运行，平台差异集中在 `desktop.py`、`desktop_window.py`、`connectors/player.py`。

## 云端只读快照

`cloud/` 下是 Cloudflare Worker，本机离线时提供只读页面，复用同一套模板。`MOVIEDB_READ_ONLY=1` 时只放行 `app.py` 中 `READ_ONLY_ENDPOINTS` 列出的 GET 端点，其余请求返回 503。新增可以只读浏览的页面时要加入这个集合；写接口不需要额外处理。
