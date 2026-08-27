# AGENTS.md

## 项目简介

当前项目是一个本地影视资源管理网页，遵循 MVC 架构原则，即Model、Connector、View原则。

当前完成了基础的 TMDB API 对接，配置好了相关API等，经测试也可以接入 AList，下一步是继续完善 @helpers.py，创建本地数据库，制作前端。

## Model

SQLite数据库存储，@helpers.py 用于对接 TMDB API、AList

SQL 代码书写准则：https://www.sqlstyle.guide/zh/

## Connector

采用 flask +jinja 结构

## View

不要花哨、绚丽，使用 html、css、JavaScript 完成即可，适当引入第三方库用于加快进度

## 进度存储区

这里用于存放进度

## Tips

该 AGENTS.MD 需要保持简洁，不得过度约束与工程化，记录项目主干。
