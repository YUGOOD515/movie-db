# AGENTS.md

## 项目简介

当前项目是一个本地影视资源管理网页，遵循 MVC 架构原则，即Model、Connector、View原则。

录入时以 TMDB 作为影视主数据（标题、年份、分类、季集、简介、演员和图片），IMDb ID 仍是主键；IMDb 评分来自官方 `title.ratings.tsv.gz`，MDBList 提供多平台评分；AList 保存本地媒体来源。电影和剧集的共用查询、图片及数据库写入流程已经合并整理。标题图片目录以 Title ID（IMDb ID）命名存放在 static/images 目录。

## Model

业务数据只存 `library.db`，共八表（`title_basic` / `title_relation` / `title_artwork` / `title_cast` / `media_file` / `playback_progress` / `collection` / `collection_item`）。`title_basic` 保存 TMDB 字段及 IMDb 评分（`average_rating`、`num_votes`），`title_cast` 保存电影和剧集的主要演员。单集直接保存季号与集号，不设置 season 和 collection 实体。读操作集中在 @queries/，写操作集中在 @models/library.py；@connectors/ 对接 TMDB、IMDb 评分文件、MDBList、AList、ffprobe 与本机播放器（MPC-HC / IINA / mpv / VLC），@services/ 编排录入和播放业务。

SQL 代码书写准则：https://www.sqlstyle.guide/zh/

录入规则：中文名按 TMDB zh-CN → TW → HK 回退；分类按 `tmdb.GENRE_NAMES` 转成 IMDb 英文分类；没有 IMDb ID 的单集不录入。重新录入时库内已有的名称、分类和季集关系保持不变，评分缺失时保留旧值。评分文件缓存在 `imdb/`（Git 忽略），超过一天自动重新下载。

待关联识别的本机状态（扫描目录、扫描快照、识别缓存、忽略列表、已确认别名、源文件健康、TMDB 查询缓存）单独存于 `discovery.db`（@models/discovery_store.py），不写入 `library.db`，以免触发云端同步。识别流程在 @services/recognition.py（名称清洗在 @services/name_cleaning.py），只产出建议，入库仍走 `media_association.complete`；AI 通过 @connectors/llm.py 调用 OpenAI 兼容接口，结果必须经 TMDB 校验。批量识别 `recognize_many` 是规则 / AI / 核对三段流水线，外部请求的速率与并发统一由 @connectors/throttle.py 控制（429 时并发自动减半），不要在调用方另加限速。

关联任务由 @services/import_jobs.py 按根 Title ID 调度，通过 @models/import_store.py 持久化到 `discovery.db`；探测、图片与派生处理共用 @services/import_runtime.py 的有界资源池。核心关联提交后继续补齐资料，恢复时复用探测检查点；签名链接与令牌不存入任务。MDBList 请求预算及评分缓存存于 `api-usage.db`（@models/api_usage.py），重试也消耗预算，不进入云端快照。

## Connector

采用 Flask + Jinja 三层结构：@app.py 路由只做"查询 → 整理 → 渲染"编排；@queries/ 只查数据库，@viewmodels/ 把查询结果整理成页面形状（poster/backdrop 卡片、进度、徽章、播放文案），Jinja 只负责 HTML 显示，不做业务判断。

## View

使用 HTML、CSS、JavaScript 完成即可，适当引入第三方库用于加快进度。当前已有精简的 Bootstrap 媒体关联页面。

前端优先使用 Bootstrap 原生组件和工具类，仅为媒体展示与必要交互保留少量自定义 CSS；本项目不使用 `design-taste-frontend` skill。

布局沿用 12 栏网格：`style.css` 顶部定义圆角等令牌与 `--grid-col` / `--grid-gap`，跨栏尺寸写成 `--grid-col` 的倍数。通用模板片段在 `templates/_ui.html`；`site.js` 只放全站行为，页面脚本按页拆分（`detail.js`、`manager.js` 等）。

动效集中在 `effects.js`（BlurHash 占位、海报倾斜、入场动画、页面共享元素转场），首页轮播在 `home.js`；第三方库放在 `static/vendor/`（见其 README），不走 CDN；评分图标放在 `static/icons/ratings/` 随仓库提交（安装包只打包 git 跟踪的文件）。海报 / 背景图的环境色与 BlurHash 存于 `static/thumbnails/tones.json`，录入时生成、启动时补齐，模板用 `image_tone` 过滤器读取。

## 设置

可配置项以 @services/settings.py 的 `SECTIONS` 清单为唯一来源：设置页按它渲染、保存时按它校验并写入 `.env`、测试核对 `.env.example`；新增环境变量时登记到清单并补进 `.env.example`。保存后由 `settings.apply` 让修改立即生效，只有标记 `restart` 的项需要重启（桌面版通过 `app.config["RESTART_HANDLER"]` 重启）。首次引导 `/setup` 只在媒体库建好前出现。

TMDB 密钥：用户填写的 `TMDB_TOKEN` 优先，留空时用安装包内置的 `connectors/tmdb_builtin.py`（Git 忽略，CI 构建时由仓库 Secret `TMDB_BUILTIN_TOKEN` 经 `packaging/build_windows.py` 的 `write_builtin_token` 写入；源码运行没有内置密钥）。`tmdb.auth()` 按格式区分 v3 API Key（查询参数）与读访问令牌（Bearer）；接口地址是官方域名时，连不上会在 `api.themoviedb.org` 与 `api.tmdb.org` 之间自动切换（`tmdb.use_origin`）。图片可能走第三方 CDN（wsrv.nl），下载图片时不要带上密钥。

文件位置统一从 @services/paths.py 取：程序文件在 `paths.APP_DIR`，用户数据（`library.db`、`discovery.db`、`.env`、`static/images`、`static/thumbnails`、`imdb/`、`logs/`）在 `paths.DATA_DIR`（源码运行时两者相同，安装版分开）；不要再用 `Path(__file__)` 拼数据路径。代码要同时能在 Windows 与 macOS 上运行，平台差异集中处理（如 `desktop.py`、`desktop_window.py`、`connectors/player.py`）；桌面版界面是 pywebview 原生窗口，页面里通过 `window.pywebview.api` 调用的接口在 `desktop_window.Api`。

## 云端只读快照

`cloud/` 下为 Cloudflare Worker：本机离线时提供只读快照，界面复用同一套模板。只读模式由 `MOVIEDB_READ_ONLY=1` 开启，只放行 `READ_ONLY_ENDPOINTS` 中的 GET 页面；新增只读页面时须加入该集合，写操作无需额外处理。

## 进度存储区



## Tips

该 AGENTS.MD 需要保持简洁，不得过度约束与工程化，记录项目主干。
