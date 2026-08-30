# AGENTS.md

## 项目简介

当前项目是一个本地影视资源管理网页，遵循 MVC 架构原则，即Model、Connector、View原则。

当前以本地 IMDb 数据库作为影视主数据，TMDB 仅补充简介、标语、主页、状态、原始语言、完整单集日期和图片；AList 保存本地媒体来源。电影和剧集的共用查询、图片及数据库写入流程已经合并整理。图片目录以 Title ID（IMDb ID）命名：`static/images/{title_id}/{posters|backdrops|logos}/{N}.{jpg|png}`，单集剧照为 `static/images/{series_title_id}/{episode_title_id}-still.jpg`，路径记录在 `title_artwork.local_path`（迁移脚本见 @scripts/migrate_artwork_paths.py）。

## Model

使用 SQLite 双库结构：`imdb.db` 是只读主数据，`library.db` 是七表业务库（`title_basic` / `title_relation` / `title_artwork` / `media_file` / `playback_progress` / `collection` / `collection_item`）。`title_basic` 直接保存 TMDB 补充字段（`tmdb_id`、`overview`、`tagline`、`original_language`、`average_rating`、`num_votes`）。单集直接保存季号与集号，不设置 season 和 collection 实体。读操作集中在 @queries/（按页面组织），写操作集中在 @models.py；@helpers.py 负责对接 TMDB、AList 及图片下载。

SQL 代码书写准则：https://www.sqlstyle.guide/zh/

## Connector

采用 Flask + Jinja 三层结构：@app.py 路由只做"查询 → 整理 → 渲染"编排；@queries/ 只查数据库，@viewmodels/ 把查询结果整理成页面形状（poster/backdrop 卡片、进度、徽章、播放文案），Jinja 只负责 HTML 显示，不做业务判断。

## View

不要花哨、绚丽，使用 HTML、CSS、JavaScript 完成即可，适当引入第三方库用于加快进度。当前已有精简的 Bootstrap 媒体关联页面。

前端优先使用 Bootstrap 原生组件和工具类，仅为媒体展示与必要交互保留少量自定义 CSS；本项目不使用 `design-taste-frontend` skill。

## 进度存储区

当前阶段：`library.db` 数据迁移全部完成——521 个 title（9 电影 / 22 剧集 / 490 单集）、854 图片、728 媒体文件、15 观看记录与收藏夹已全部录入，与遗留库行数完全对齐；provisional 单集已通过本地 `title_episode` 表完成 IMDb 匹配（@scripts/match_provisional_episodes.py，遗留库中已无 provisional 记录）。应用读写已切换到 `library.db`，`movie.db` 遗留读写代码与导入脚本已删除，旧评分缓存流程已清理。

下一阶段：接入新版录入写入库链路（当前 @templates/media.html 关联表单为禁用占位）。

## Tips

该 AGENTS.MD 需要保持简洁，不得过度约束与工程化，记录项目主干。
