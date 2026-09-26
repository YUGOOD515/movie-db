# 审计发现

## Requirements
- 阅读当前 MovieDB 项目，而不是只给通用 OOP 建议。
- 目标是精简、标准化、易懂；类是候选手段，不是预定答案。
- 输出可执行计划，本轮不修改业务代码。

## Initial Context
- 项目规定 `app.py` 只做查询、整理、渲染编排；`queries/` 查库，`viewmodels/` 塑形，`models/library.py` 写库，`services/` 编排业务。
- 当前工作树已有用户未提交改动：`static/site.js`、`static/style.css`、`templates/detail.html`、`tests/test_library_app.py`、`viewmodels/title.py`，另有 `.planning/next_step/`。
- 既有项目记录强调避免重型 ORM、Repository 层和巨型 movie/TV 分支；当前审计需要以实时代码复核。

## Research Findings
- 当前生产代码并非完全函数式：`services/media_import.py` 已有 `ImportBundle`、`SeriesImportContext`、`PreparedMediaImport` 三个 dataclass；`models/profiling.py` 通过继承 SQLite connection/cursor 封装横切行为。现有类主要用于“持有跨步骤状态”或“适配框架接口”，边界合理。
- `app.py` 的路由基本维持查询 -> ViewModel -> 渲染；把“电影”做成带查询、写库、播放方法的 Active Record 会重新混合这些边界。
- 数据库模型以统一 `title_basic` 表承载 movie/series/episode；因此单独 `Movie` 类会迫使相同字段和行为在 `Series`/`Episode` 再实现一次。
- 当前最明显的复杂点是 `viewmodels/title.py`：约 792 行、以嵌套 dict 传递隐式数据契约。复杂度来自“页面状态塑形”，不是缺少电影领域对象。
- `services/media_import.py` 已经在准备阶段使用 dataclass 传递 bundle/context/prepared import；其中 `SeriesImportContext` 还持有 `images_prepared` 这一真实可变状态，是优先完善现有类、而非另造 Movie 类的候选。
- `queries/title.py` 返回页面查询所需的聚合原始 dict，`viewmodels/title.py` 再生成 Jinja dict。若直接把查询结果全面类化，会增加 sqlite Row -> class -> dict 的转换而不减少模板复杂度。
- `models/library.py::import_media_bundle` 同时支持自管连接和加入外部事务，这是有意支持单项/批量两条路径；重构时不能把连接生命周期藏进实体类。
- 规模证据：生产代码中 `services/media_import.py` 796 行、`viewmodels/title.py` 792 行、`models/library.py` 517 行、`app.py` 429 行。最长主干分别是批量导入 128 行、标题 ViewModel 136 行、评分格式化 117 行，以及写库 bundle 219 行。
- dict 契约最密集的模块也是上述编排/塑形模块：`media_import.py` 有 24 个 dict literal，`viewmodels/title.py` 有 30 个；相比之下 queries 主要是 SQL 与薄转换。若要标准化类型，应从模块内部边界开始，而不是一次性全项目类化。
- `services/rating_sync.py` 的 `_job_lock` 与 `_job_state` 是同生命周期的全局状态；它比 Movie 更符合对象封装条件，但当前只有单进程单任务实例，模块级单例已经足够。只有引入多任务/可注入测试实例时再升级为类。
- `connectors/player.py` 同样以模块级 lock + generation 管理唯一 MPC-HC 会话。当前系统明确只有一个播放器，创建 `Player` 类不会减少逻辑；多播放器或多用户出现时再类化。
- `connectors/mdblist.py` 依赖 `scripts/fetch_multi_platform_ratings.py` 中的不可变评分 dataclass。类本身合理，但生产 connector 反向依赖脚本目录是更直接的标准化问题，应优先把共享评分模型/解析逻辑移入 connector 或 service，而不是添加 Movie 类。
- `viewmodels/home.py`、`viewmodels/manager.py` 与 `viewmodels/title.py` 重复构造标题、图片、URL 等页面字段，优先考虑扩充已有 `build_poster_card`/小型共享格式化函数；不要为了去掉 dict 而建立庞大 ViewModel 类层次。
- 当前未提交的详情页改动只调整“全剧/本集”默认显示、hero 图片优先级和 rail 间距；这些是用户现有 UI 工作，不应纳入或覆盖本次架构计划。
- 调用面较窄：`query_title` 与 `build_title_viewmodel` 只在标题详情/续播路由及集成测试使用；媒体导入的公共入口主要由路由和少量服务级测试调用。适合小步迁移并保留函数门面。
- 运行 `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`：52 个测试全部通过，耗时约 4.2 秒，可作为重构基线。
- 精确行段：输入解析/标准化位于 `app.py:317-341`、`services/media_import.py:634-666`；标题解析位于 `services/media_import.py:48-92,323-345`；现有导入 dataclass 位于 `services/media_import.py:258-283`；单项/批量主流程位于 `services/media_import.py:566-631,669-796`。
- 类化不能等同于减行：`MediaImportRequest` 与 `ResolvedTitle` 预计主要改善类型契约，兼容式改造的净行数很可能在 `-5` 到 `+15`；把现有函数机械搬为方法不会产生真实节省。
- 若增加 `MediaImporter` 服务类，虽然可持有三条数据库/图片路径，但当前只有一个实例，且单项/批量真正可复用的主要是约十余行资源打开代码；类定义、初始化与兼容门面会抵消节省，暂不推荐。
- 现有 `ImportBundle`、`SeriesImportContext`、`PreparedMediaImport` 已经覆盖跨阶段数据。合理的小改是让 `SeriesImportContext.resolve()` 接管只依赖自身索引的解析逻辑，以及让 `PreparedMediaImport.to_result()` 接管结果映射；二者主要是内聚性收益，预计合计只减少约 2-8 行。
- 真正可见的减行点仍是纯函数：`viewmodels/title.py::_format_ratings` 当前 117 行，规则表化预计可减少 45-65 行；`viewmodels/manager.py::build_manager_viewmodel` 的三组图片映射预计可减少 15-25 行。这些不应伪装成类设计收益。
- AST 检查确认 `ResolvedTitle` 现有字典中的 `relation` 键没有下游读取；relation 仅在解析函数内用于校验。新对象应删除该字段，并避免添加只代理一层字典索引的 `root_id`/`target_id` 属性。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 优先检查 `dataclass`/不可变值对象，而非带数据库连接的 Active Record | 可标准化字典形状，同时保持现有分层与 SQLite 查询方式 |
| 暂不建议创建 `Movie` Active Record | 数据库与页面都以统一 title 为中心，类化会复制 series/episode 逻辑并跨越 query/model/service 边界 |
| 首批候选聚焦边界数据与现有 import 上下文 | 这里已有重复参数、稳定字段和真实生命周期，收益更明确 |
| 模块级单例状态暂不强制类化 | rating refresh 与 MPC 当前都只有一个实例，module 已提供足够封装；类化只会增加调用层 |
| 先修正生产代码依赖 scripts 的方向 | 这是可验证的依赖边界问题，比全面 OOP 改写收益更直接 |
| 首个类化试点使用 `MediaImportRequest` 与 `ResolvedTitle` | 两者有稳定字段和明确不变量，且影响可限制在一个 service 内 |
| Jinja-facing ViewModel 继续返回 dict | 模板原生适配映射；全面对象化只会增加转换和属性兼容成本 |
| 不增加 `MediaImporter` 服务类 | 单例配置对象和兼容门面会抵消仅十余行的资源打开重复；现阶段函数模块已经足够封装 |
| `SeriesImportContext` 只吸收直接操作自身索引/可变字段的方法 | `resolve` 和 `prepare_images` 有明确对象归属；数据库写入仍留在 service/model |
| 行数目标按完整改造计约 55-90 行 | 类化接近持平，主要减量来自评分规则和 manager 图片映射去重 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 当前活动 planning 目录属于另一任务 | 新建独立目录，不覆盖 `.planning/.active_plan` |
| `.venv` 未安装 pytest | 使用标准库 `unittest discover`，不为审计新增依赖 |

## Resources
- `AGENTS.MD`
- `app.py`
- `queries/`、`viewmodels/`、`models/`、`services/`、`connectors/`、`tests/`
