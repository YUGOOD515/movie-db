# MovieDB 功能完善路线建议

## Context

当前项目已经不是简单的影视海报展示页，而是一个可日常使用的、单用户本地优先的影视库：它已经打通 IMDb/TMDB/MDBList 元数据、AList 媒体、详情与季集浏览、MPC-HC 播放、进度回写、收藏、图片管理和批量录入。

现在的“不够完善”主要不是页面数量不足，而是长期使用所需的几个闭环尚未闭合：作品越来越多后不容易找；已经记录观看进度却缺少观看管理；收藏表结构已存在但交互只是固定“喜欢”；批量入库和媒体失效后缺少可诊断、可恢复的管理工具。

建议继续坚持 Flask/Jinja + SQLite、单用户、本地优先、AList + 桌面播放器的定位，先把个人影视库做好，不急于复制 Plex/Jellyfin 的转码、多用户与远程媒体服务器能力。

## 当前成熟度判断

可视为约 v0.6–v0.7：核心观看链路完整，但发现、观看状态和库维护链路还不完整。

现有主闭环：

* 入库：IMDb ID + AList 直链 → ffprobe → TMDB 图片/演员 → SQLite
* 浏览：首页 rails、最近添加、未观看、高分、类别筛选、收藏页
* 详情：电影/剧集、季集切换、演员、评分、多个媒体版本、媒体参数
* 播放：服务端启动 MPC-HC，并把进度写回 `playback_progress`
* 管理：作品检索、主图选择、全库评分刷新、最多 100 条批量导入

# 推荐路线

## P0：优先补齐日常使用闭环

### 1. 全局搜索与统一结果页（高价值 / 低—中成本）

**场景：** 用户只记得中文名、原名、演员或 IMDb ID，不应先猜作品在哪个首页区块或筛选类别。

**MVP：**

* 导航栏常驻搜索入口；
* 搜索 `display_title`、`primary_title`、`original_title`、IMDb ID，可补充演员名；
* 结果按电影/剧集区分，支持打开详情；
* 与分页同时实现，暂不引入 Elasticsearch、拼音库或复杂推荐。

可复用管理页已有搜索模式：`queries/manager.py`、`templates/media.html`、`app.py:227-253`。

### 2. 列表分页并保留筛选状态（高价值 / 低成本）

**场景：** 当前 catalog、收藏、最近添加、未观看和管理结果均可能一次返回全部内容；库继续增长后影响浏览和响应。

**MVP：**

* 服务端分页，每页约 24–30 部；
* URL 保留类型、类别、年份、语言、评分、排序和搜索词；
* 显示总数、当前页、上一页/下一页；
* 首页 rails 继续保持少量内容，不做分页。

关键现状见 `app.py:88-134`、`queries/catalog.py`、`templates/genre_page.html:11-130`。

### 3. “观看中心”：继续观看、历史与手工状态（高价值 / 中成本）

**场景：** 数据库已保存进度并能自动续播，但用户无法集中查看“看到哪里、最近看过什么、哪些已经看完”。

**MVP：**

* 独立“继续观看”和“观看历史”页；
* 电影/单集支持“标记已看”“取消已看”“从头播放”；
* 剧集显示最近观看集、下一集建议与整体进度；
* 明确定义完成状态，不能只依赖“存在过任意播放记录”。

应复用 `queries/home.py:7-66` 的最近观看查询、`viewmodels/title.py` 的 90%/剩余时间完成判断，以及 `models/library.py` 的进度写入逻辑。

现有 `queries/home.py:96-131` 把“有任意播放记录”当作已观看，需在此功能中统一语义。

### 4. 从“喜欢”升级为真正的片单系统（高价值 / 中成本）

**场景：** 用户可创建“稍后观看”“周末看”“与家人看”等清单，并在详情页选择加入哪个片单。

**MVP：**

* 创建、重命名、删除自定义收藏夹；
* 详情页由单一心形按钮升级为收藏夹选择器；
* 支持从片单移除作品；
* 系统“喜欢”收藏不可删除；
* 暂不做共享、拖拽排序、智能片单或协作。

现有 `collection / collection_item` schema 已基本满足数据需求，主要扩展：

`models/library.py`、`queries/collection.py`、`viewmodels/collection.py`、`templates/favorites_page.html` 和 `templates/detail.html`。

### 5. 写操作安全基线（基础项 / 低—中成本）

这不是面向用户的新卖点，但应随下一轮功能一起完成：

* 所有 POST 与前端 fetch 加 CSRF；
* 默认只监听本机回环地址；
* 文档明确开放到 LAN/公网前必须增加认证；
* 当前不做完整多用户、OAuth 或角色权限。

涉及 `app.py:164-174, 255-414`、各动态表单及 `static/site.js`。

## P1：让入库和维护变得可信、可恢复

### 6. 批量导入预检与逐行结果（高价值 / 中成本）

**MVP：**

* 正式写入前 dry-run：校验 IMDb/季集、AList 可达、路径重复、目标关联和关键元数据；
* 每行显示“可导入 / 警告 / 阻断”；
* 执行后保留成功、跳过、失败的逐项报告；
* 失败时保留用户输入并允许修正后重试；
* 第一版仍可保持全批事务，避免半成品数据库。

复用 `services/media_import.py` 已有的 normalize、prepare、probe 和事务逻辑；改造入口为 `app.py:255-341`、`templates/media.html:146-187`、`static/site.js`。

### 7. AList 扫描发现与待关联队列（高价值 / 中—高成本）

**MVP：**

* 配置一个或多个 AList 根目录；
* 递归发现数据库中尚未关联的视频文件；
* 生成“待关联”列表；
* 可根据文件名给出候选，但必须人工确认后进入现有导入流程；
* 不静默自动匹配、不自动删除远端文件。

应在导入预检稳定后做；复用 `connectors/alist.py` 与 `services/media_import.py`，避免另建一套录入链路。

### 8. 媒体健康诊断与安全修复（高价值 / 中—高成本）

**MVP：**

* 管理页显示 AList 路径失效、探测信息缺失、时长异常、缺主图/元数据等状态；
* 支持重新验证、重新 ffprobe、修改关联路径、重新抓取元数据与图片；
* 支持删除“本地关联记录”，但不删除 AList 远端文件；
* 所有破坏性操作二次确认并给出影响范围。

主要涉及：

`queries/manager.py`、`templates/media.html`、`models/library.py`、`services/media_import.py`、`connectors/alist.py`、`connectors/media_probe.py`。

### 9. 数据库迁移与升级前备份（基础项 / 中成本）

后续观看状态、任务和健康字段都会改 schema，应先建立可靠升级路径：

* 增加 schema version；
* 启动时按顺序执行幂等迁移；
* 迁移前自动备份 `library.db`；
* 失败时停止启动并显示明确错误；
* 不为此引入 ORM 或重型迁移框架。

现状见 `migrations/library_schema.sql:1-6`、`models/database.py`。

## P2：基础闭环稳定后的增强

### 10. 持久化任务中心（中价值 / 中—高成本）

统一承载评分刷新、AList 扫描、健康检查、元数据重抓：记录状态、进度、开始/结束时间和逐项错误；重启后把进行中的任务标为中断。

当前规模优先用 SQLite + 受控单进程任务，不引入 Redis/Celery。

现有评分任务入口为 `app.py:343-354`、`services/rating_sync.py`。

### 11. 详情信息补全与元数据新鲜度（中价值 / 低—中成本）

分两步做：

1. 先展示已经采集但遗漏的 MDBList、MyAnimeList 等评分；
2. 再扩充年龄分级、国家/语言、主创、电视网、制作公司、数据来源与更新时间，并提供单条重抓入口。

现状见 `migrations/library_schema.sql:27-45` 与 `viewmodels/title.py`：部分评分已有字段但未展示，`age_rating`、`creators`、`networks` 仍为空占位。

### 12. 更清晰的媒体版本、音轨和字幕信息（中价值 / 中成本）

* 多版本菜单至少显示文件名、大小、分辨率、编码和路径摘要；
* ffprobe 扩展为完整音轨/字幕流采集；
* 支持选择或记住首选版本；
* 不在第一步承诺浏览器内切换音轨或转码。

### 13. 移动端浏览与触屏可用性（中价值 / 低—中成本）

作为并行质量工作：

* 横向 rail 增加 edge fade/箭头/scroll-snap；
* 媒体图片操作不能只依赖 hover；
* 修复长简介遮罩层级；
* 完善导航返回和 active 状态。

实施时必须保留当前用户在 `static/style.css` 中尚未提交的修改，不得覆盖。

### 14. 多终端播放只做受控评估（高潜在价值 / 高成本）

若确有手机/平板播放需求，先提供外部播放器交接或安全直链，不立即建设内置 Web 播放、实时转码、字幕烧录、DLNA 等完整媒体服务器能力。

当前的 `connectors/player.py` 明确是服务器本机 MPC-HC 模式。

# 当前不建议优先投入

* 完整多用户账号、社交共享和复杂权限；
* 自建浏览器播放器、实时转码和硬件加速；
* 无人工确认的文件名自动匹配与自动入库；
* Redis/Celery/Kafka 等重型任务基础设施；
* 推荐算法、外部评论聚合和年度报告；
* 重构为 SPA、前后端分离或替换 ORM。

这些方向都容易放大维护成本，却不能先解决“找片、续看、归类、可靠入库和资源失效修复”。

# 推荐首个版本包（约 2–4 周）

**目标闭环：**

搜到作品 → 进入详情 → 播放/续播 → 管理观看状态 → 放入真正可用的片单。

**建议范围：**

1. 全局搜索与统一结果页；
2. 搜索、catalog、收藏、最近添加、未观看的服务端分页；
3. 观看中心 MVP（继续观看、历史、标记/取消已看、从头播放）；
4. 自定义收藏夹 CRUD 与详情页选择加入；
5. 为所有新旧写操作补齐 CSRF；
6. 顺手修复已确认的低成本问题：catalog 默认排序被误标 active、触屏图片操作不可达、评分刷新失败残留假进度。

**明确不塞进首包：**

AList 自动扫描、媒体删除、元数据重抓、任务中心、浏览器播放器和登录体系。

# 后续实现涉及的关键文件

* 路由与操作入口：`app.py`
* schema 与迁移：`migrations/library_schema.sql`、`models/database.py`
* 写模型：`models/library.py`
* 查询：`queries/home.py`、`queries/catalog.py`、`queries/collection.py`、`queries/manager.py`、`queries/title.py`
* ViewModel：`viewmodels/title.py`、`viewmodels/collection.py`、`viewmodels/catalog.py`
* 页面：`templates/layout.html`、`templates/index.html`、`templates/genre_page.html`、`templates/detail.html`、`templates/favorites_page.html`、`templates/media.html`
* 交互与样式：`static/site.js`、`static/style.css`
* 入库/同步：`services/media_import.py`、`services/rating_sync.py`
* 外部能力：`connectors/alist.py`、`connectors/player.py`、`connectors/media_probe.py`

# 若进入实现阶段的验证方式

1. 查询测试：搜索字段、分页边界、筛选/排序参数保留、空结果、重复标题。
2. 观看状态测试：未开始、播放中、90% 完成、手工已看、取消已看、多版本文件、剧集下一集。
3. 收藏测试：CRUD、系统收藏保护、类型约束、重复加入、删除确认和级联行为。
4. 安全测试：所有写路由缺少或错误 CSRF 时拒绝，合法表单与 fetch 正常。
5. 回归测试：运行现有测试套件，确保导入原子性、评分刷新、AList URL 校验和详情播放不回退。
6. 端到端检查：启动真实 Flask 应用，分别以桌面与 360–430px 移动视口走完“搜索 → 详情 → 收藏 → 播放/续播 → 观看中心”。
7. 数据升级演练：在 `library.db` 副本上运行迁移，核对 574 个标题、771 个媒体关联、既有收藏与播放记录数量不丢失。
