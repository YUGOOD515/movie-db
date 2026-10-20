# AGENTS.md

## 项目简介

当前项目是一个本地影视资源管理网页，遵循 MVC 架构原则，即Model、Connector、View原则。

当前以本地 IMDb 数据库作为影视主数据，TMDB 补充简介、标语、原始语言、演员和图片；AList 保存本地媒体来源。电影和剧集的共用查询、图片及数据库写入流程已经合并整理。标题图片目录以 Title ID（IMDb ID）命名存放在 static/images 目录。

## Model

使用 SQLite 双库结构：`imdb.db` 是只读主数据，`library.db` 是八表业务库（`title_basic` / `title_relation` / `title_artwork` / `title_cast` / `media_file` / `playback_progress` / `collection` / `collection_item`）。`title_basic` 保存 TMDB 补充字段（`tmdb_id`、`overview`、`tagline`、`original_language`）及 IMDb 评分字段（`average_rating`、`num_votes`），`title_cast` 保存电影和剧集的主要演员。单集直接保存季号与集号，不设置 season 和 collection 实体。读操作集中在 @queries/，写操作集中在 @models/library.py；@connectors/ 对接 TMDB、AList、ffprobe 与 MPC-HC，@services/ 编排录入和播放业务。

SQL 代码书写准则：https://www.sqlstyle.guide/zh/

`imdb.db` 仅保留 `title_basics`、`title_akas`、`title_episode`、`title_ratings`；别名只保留现有中文名称查询所需的五个字段及候选记录，由 `scripts/import_imdb.py` 维护。

## Connector

采用 Flask + Jinja 三层结构：@app.py 路由只做"查询 → 整理 → 渲染"编排；@queries/ 只查数据库，@viewmodels/ 把查询结果整理成页面形状（poster/backdrop 卡片、进度、徽章、播放文案），Jinja 只负责 HTML 显示，不做业务判断。

## View

使用 HTML、CSS、JavaScript 完成即可，适当引入第三方库用于加快进度。当前已有精简的 Bootstrap 媒体关联页面。

前端优先使用 Bootstrap 原生组件和工具类，仅为媒体展示与必要交互保留少量自定义 CSS；本项目不使用 `design-taste-frontend` skill。

布局沿用 12 栏网格：`style.css` 顶部定义圆角等令牌与 `--grid-col` / `--grid-gap`，跨栏尺寸写成 `--grid-col` 的倍数。通用模板片段在 `templates/_ui.html`；`site.js` 只放全站行为，页面脚本按页拆分（`detail.js`、`manager.js` 等）。

## 云端只读快照

`cloud/` 下为 Cloudflare Worker：本机离线时提供只读快照，界面复用同一套模板。只读模式由 `MOVIEDB_READ_ONLY=1` 开启，只放行 `READ_ONLY_ENDPOINTS` 中的 GET 页面；新增只读页面时须加入该集合，写操作无需额外处理。

## 进度存储区



## Tips

该 AGENTS.MD 需要保持简洁，不得过度约束与工程化，记录项目主干。
