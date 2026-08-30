# AGENTS.md

## 项目简介

当前项目是一个本地影视资源管理网页，遵循 MVC 架构原则，即Model、Connector、View原则。

当前以本地 IMDb 数据库作为影视主数据，TMDB 仅补充简介、标语、主页、状态、原始语言、完整单集日期和图片；AList 保存本地媒体来源。电影和剧集的共用查询、图片及数据库写入流程已经合并整理。

## Model

使用 SQLite 双库结构：`imdb.db` 是只读主数据，`library.db` 是七表业务库（`title_basic` / `title_relation` / `title_artwork` / `media_file` / `playback_progress` / `collection` / `collection_item`）。`title_basic` 直接保存 TMDB 补充字段（`tmdb_id`、`overview`、`tagline`、`original_language`、`average_rating`、`num_votes`）。`movie.db` 为遗留库，仅作数据迁移来源，不再新增写入。单集直接保存季号与集号，不设置 season 和 collection 实体。数据库操作集中在 @models.py；@helpers.py 负责对接 TMDB、AList 及图片下载；@scripts/populate_library.py 从遗留库 + IMDb + TMDB 填充 `library.db`。

SQL 代码书写准则：https://www.sqlstyle.guide/zh/

## Connector

采用 Flask + Jinja 结构，@app.py 负责请求处理与业务流程编排，不直接书写外部请求或 SQL。

## View

不要花哨、绚丽，使用 HTML、CSS、JavaScript 完成即可，适当引入第三方库用于加快进度。当前已有精简的 Bootstrap 媒体关联页面。

前端优先使用 Bootstrap 原生组件和工具类，仅为媒体展示与必要交互保留少量自定义 CSS；本项目不使用 `design-taste-frontend` skill。

## 进度存储区

当前阶段：`library.db` 数据迁移全部完成——521 个 title（9 电影 / 22 剧集 / 490 单集）、854 图片、728 媒体文件、15 观看记录与收藏夹已全部录入，与遗留库行数完全对齐；provisional 单集已通过本地 `title_episode` 表完成 IMDb 匹配（@scripts/match_provisional_episodes.py，遗留库中已无 provisional 记录）。

下一阶段：将 @models.py 与 @app.py 的读写从 `movie.db` 切换到 `library.db`，迁移完成并验证后淘汰 `movie.db`；清理已停用的旧评分缓存流程。

## Tips

该 AGENTS.MD 需要保持简洁，不得过度约束与工程化，记录项目主干。
