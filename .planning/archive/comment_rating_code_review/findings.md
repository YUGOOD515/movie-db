# Findings

- 既有架构约定：`models.py` 负责 SQLite，`helpers.py` 负责 TMDB/AList/下载，`app.py` 负责 Flask 请求编排。
- 用户偏好是合并真正共享的电影/剧集逻辑，但不制造巨型条件分支，也不因行数机械拆分。
- Git 基线为 `e0e6047 View：媒体页大幅精简，增加动效`；工作区新增评分链路主要为 app.py +68、helpers.py +268、models.py +191、schema/migration、detail 模板和测试。
- 当前主文件长度：app.py 1012 行、helpers.py 1000 行、models.py 3037 行；tests/test_app.py 2348 行。长度本身不是缺陷，但 models.py 和单体测试文件已达到需要关注导航与聚合边界的规模。
- `scratch/devserver.log` 自基线增加 1165 行且处于跟踪修改状态，另有测试数据库和 scratch 文本未跟踪；属于提交卫生风险，不是评分业务逻辑问题。
- 新增 OMDb API key 采用环境变量但同时带有源码内明文默认值（helpers.py 顶部）；这会把凭据带入提交历史，属于高优先级配置/安全问题。
- `get_media_external_ids()` 将网络/API 失败吞成空字典；`fetch_media_ratings()` 随后把它当成“没有 IMDb ID”的正常空结果，`save_media_ratings()` 又写入时间戳，导致临时失败可能被永久缓存为已完成。
- 媒体评分缓存仅判断 `ratings_updated_at` 是否非空，没有 TTL 或强制刷新；评分、票数等会永久冻结。单集缓存只要同季任意一集有时间戳就返回 true，并在保存后把所有未匹配单集也标记为已更新，新增/部分缺失单集可能永不补抓。
- app.py 电影与剧集导入中重复了“检查缓存 -> 抓取 -> 保存 -> 捕获异常”的编排；剧集又重复读取 external_ids。该逻辑已经足够独立，适合提取小型评分同步服务/编排函数，而不是继续堆在导入函数里。
- `fetch_omdb_ratings()` 与 `fetch_omdb_season_episodes()` 重复 API key 解析、HTTP 请求、Response=False 分类与异常包装，属于明显的外部客户端重复。
- 新增前端在电影/剧集 Hero 和每个季摘要中重复书写 IMDb/RT/Metacritic/TMDB 条件与标记；剧集的系列级 IMDb/RT/Metacritic 被复制到每一季摘要，容易让人误认为季级评分。
- `rotten_tomatoes_popcornmeter` 从解析器开始始终固定为 None，正常抓取链路没有任何来源；但数据库、模型、模板和测试为它铺设了完整字段与展示分支。若没有近期数据源计划，这是典型的预留式过度工程；现有测试通过手工写值验证展示，不能证明真实链路可产出该值。
- 新增测试方法本身普遍过长：评分存储 124 行、季评分管线 124 行，多个导入测试 80-119 行；既有两项导入测试甚至 389/430 行。大量重复媒体详情和 source metadata 字典使测试可读性及失败定位明显变差。
- `models.py` 的新增评分持久化位置和职责是合理的，但 `has_movie_ratings` / `has_series_ratings` 只是对通用函数的一行包装，收益有限；单集逐条 UPDATE 对一季几十集规模可接受，不必为了性能过度抽象。
- 三文件 AST 审计：app.py 最长函数 97 行，helpers.py 最长 89 行，整体尚可；models.py 的 `get_media_detail()` 378 行、`get_media_overview_data()` 239 行，属于既有集中式查询/组装技术债。应按查询与结果整形的自然边界拆私有函数，不应为了行数引入 Repository/Service 多层架构。
- 25 项单元测试全部通过（31.668 秒）；`git diff --check` 只报告 models.py 与 tests/test_app.py 文件尾新增空行，以及工作区换行符警告，没有语法性空白错误。
- 现有测试未覆盖：TMDB external_ids 临时失败被写成成功缓存、OMDb 仅返回部分季单集、后续新增单集、评分缓存过期/强制刷新、真实抓取链路产生 Popcornmeter。详情页测试只做整页字符串包含判断，无法确认分数归属于正确评分来源或正确层级。
