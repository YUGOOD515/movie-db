# AGENTS.md

## 项目简介

当前项目是一个本地影视资源管理网页，遵循 MVC 架构原则，即Model、Connector、View原则。

当前已完成电影与剧集单集的 TMDB、AList 对接，可保存媒体详情、季与单集数据、AList 文件对应关系，并将中文海报、中文 Logo、背景图及单集剧照下载到本地。电影和剧集的共用查询、图片及数据库写入流程已经合并整理。

## Model

使用 SQLite 存储电影、剧集、季、单集、图片和 AList 来源关系，数据库操作集中在 @models.py；@helpers.py 负责对接 TMDB、AList 及图片下载。

SQL 代码书写准则：https://www.sqlstyle.guide/zh/

## Connector

采用 Flask + Jinja 结构，@app.py 负责请求处理与业务流程编排，不直接书写外部请求或 SQL。

## View

不要花哨、绚丽，使用 HTML、CSS、JavaScript 完成即可，适当引入第三方库用于加快进度。当前已有精简的 Bootstrap 媒体关联页面。

前端优先使用 Bootstrap 原生组件和工具类，仅为媒体展示与必要交互保留少量自定义 CSS；本项目不使用 `design-taste-frontend` skill。

## 进度存储区

当前阶段：电影和剧集录入、图片本地化、数据库关系存储及基础关联页面已经完成。

下一阶段：整理现有影视数据并规范入库，在此基础上制作媒体资源浏览前端。

## Tips

该 AGENTS.MD 需要保持简洁，不得过度约束与工程化，记录项目主干。
