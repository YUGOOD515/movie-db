# AGENTS.md

## 项目简介

本地影视资源管理网页，Flask + Jinja 实现，按 Model / Connector / View 分层。影视资料以 TMDB 为准，IMDb ID 是主键；媒体文件来自内置网盘层或 AList / OpenList 透传。

本文件只写我的偏好和必须遵守的约定，细节在设计文档里，改到相关部分时先读对应文档：

- [docs/design/frontend.md](docs/design/frontend.md)：网格与间距、管理页 Design system、组件与脚本
- [docs/design/backend.md](docs/design/backend.md)：分层、数据库、SQL 书写、录入规则、外部服务、播放来源
- [docs/design/api.md](docs/design/api.md)：路由清单、错误格式、状态码、安全检查
- [docs/zh-CN/development.md](docs/zh-CN/development.md)：运行、测试、打包、发布

`.planning/` 保存整个开发历史中的全部计划文件（每个任务一份 `task_plan.md` / `findings.md` / `progress.md`），其中很多方案已经被推翻或重构。参考时先看文件的最后修改时间（`git log -1 --format=%cs -- <目录>`），越早的越可能过时；与代码或设计文档冲突时，以代码和设计文档为准。

## 常用命令

- 启动：`uv run flask --app app run --host 127.0.0.1 --port 5055`
- Python 测试：`uv run --with pytest python -m pytest -q`（单个文件把路径加在最后）
- 前端规则测试：`node --test tests/test_filename_rules.cjs`

改完代码后跑相关测试；改了页面就打开页面看一眼效果。

## Model

- 读写分开：页面读 `library.db` 的 SQL 放 `queries/`，写操作放 `models/`。
- SQL 按 https://www.sqlstyle.guide/zh/ 书写，现有写法参考 `queries/home.py`；值用参数传入，不拼进字符串。
- 媒体展示数据放 `library.db`；凭据和本机状态放 `local.db`，因为 `library.db` 会同步到云端快照。

## Connector

这里的 Connector 指路由编排层（`app.py`），不是对接外部服务的 `connectors/` 目录。

- 路由只做“查询 → 整理 → 渲染”：`queries/` 查库，`viewmodels/` 整理成页面数据，Jinja 只负责显示。路由里不写 SQL，也不拼页面数据。
- 业务流程放 `services/`，外部服务调用放 `connectors/`，限速统一交给 `connectors/throttle.py`。

## View

- 用 HTML、CSS、JavaScript 完成，前端优先使用 Bootstrap 原生组件和工具类；只有媒体展示和 Bootstrap 做不到的交互才写自定义 CSS。不使用 `design-taste-frontend` skill。
- 可以引入第三方库来加快进度，放进 `static/vendor/` 并在其 README 登记，不走 CDN。
- 卡片要对齐成竖线：内容包在 `container-xl` 里；卡片水平间距全站统一，小于 992px 用 1rem、992px 及以上用 1.5rem（网格 `g-3 g-lg-4`，轨道 `gap-3 gap-lg-4`）；按栏定宽时用 `--grid-col` / `--grid-gap` 计算。完整规则见 frontend.md。
- 设置页、网盘页等管理页面复用 `ui-*` 类和 A–D 对齐线，不另写一套。
- 界面文案用中文。

## 进度存储区



## Tips

- 保持简单，不过度约束、不过度工程化：只做需求要的改动，不顺手重构或加配置项。
- 代码要在 Windows 和 macOS 上都能运行。
- 提交信息用中文，第一行写成用户能看懂的一句话，概括改了什么、为什么改。CHANGELOG 不逐次更新，发版时直接用提交信息汇总（步骤见 development.md 的“持续集成与发布”）。
- 修改 `docs/zh-CN/` 时，在同一次提交里同步修改 `docs/en/` 的对应段落。
- 改变某项约定时，同步更新对应的设计文档；本文件只记录项目主干，保持简短。
