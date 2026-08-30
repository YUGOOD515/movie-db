# 审查发现

- `query.md` 要求 route -> query -> viewmodel -> Jinja，queries 只查数据，viewmodels 解释数据形状，Jinja 只显示。
- 当前工作区已有 `templates/_media_cards.html` 未提交修改；审查保留该状态。
- 主读取链路总体已落地：`db.py` 为只读请求连接，核心页面均调用 `queries/` 与 `viewmodels/`，query 层未发现 Python 循环逐项查库的 N+1。
- 高严重度：`helpers.py:15-19` 在已跟踪源码中硬编码 AList 身份信息与 TMDB bearer token；`.gitignore` 也未提供 `.env` 约定。
- `viewmodels/title.py` 565 行，存在 `_legacy_source/_legacy_episode/_legacy_seasons` 二次适配；同一单集又生成 `episodes -> legacy seasons -> episode_data` 三套相近形状，属于明显过渡性重复。
- `query.md` 明确要求区分 `selected_episode` 与 `resume_episode`；当前只实现 selected，默认详情 S1E1 时不会另行提供最近观看单集的继续播放入口。
- 电影播放文案只检查 `position_ms`，未检查 `progress.completed`；播放 route 对 >=95% 会从头开始，但页面仍显示“继续播放”。
- `app.py` 的 manager route 在 ViewModel 返回后再写 `page["active_tab"]`，读取 route 仍含页面形状整理；收藏页 route 直接拼三个 query，而非页面级 `query_collection`。
- 管理页同时做 SQL 端筛选与当前结果集上的客户端即时筛选；在带 q/type 的 URL 上清空或切换条件无法恢复服务器未返回的数据，交互语义不一致。
- `_media_cards.html` 的 `overview_card(item, query="", media_filter="all")` 后两参数从未使用；模板直接读取 `request.full_path`，且以 `position == 1` 推断当前图片，部分页面逻辑未收敛到 ViewModel。
- `static/site.js:39-44` 的详情页正则仍只识别旧 `/movies/<数字>`、`/series/<数字>`，不识别主入口 `/title/<title_id>`，导致详情页侧栏首页项不再激活。
- 多图片保存不具备事务原子性：route 逐项调用各自开事务的 `set_library_primary_artwork()`；临时库复现为 poster 已提交、随后非法 backdrop 导致 404，最终保留半套修改。
- 收藏 episode 时，query 映射为 series 卡片但保留季集 URL；临时库加入同剧 S1E1/S1E2 后得到两个标题与海报完全相同的“小谢尔顿”卡片，仅链接参数不同，前端无法辨认收藏的是哪一集。
- 13 项现有测试全部通过，compileall、两个 JS `node --check`、`git diff --check` 均通过；正式 `library.db` SHA-256 前后相同。通过不覆盖上述语义缺口。
- 集成测试依赖本机且被忽略的生产 `library.db`，不存在时在 `setUp()` 中逐项 skip；干净环境可在未覆盖路由/query/ViewModel 的情况下仍显示测试成功，且固定断言 521 条数据，测试可移植性较弱。
- `static/home.js` 负责图片错误 fallback，但详情页未加载它，`site.js` 动态创建的 episode backdrop 也未绑定 error handler；当前资源完整时不触发，缺图时 fallback 行为不完整。
- `helpers.py` 中多组为下一阶段录入链路准备的 TMDB/AList/下载函数目前只有脚本或测试调用，部分完全没有产品调用；属于待接入代码，不宜一概删除，但应在录入链路确定后清理未使用 API。
- `build_media_badges()` 为每个 source 计算 `badges`，但全项目没有消费该字段；详情模板与 `site.js` 又分别按 resolution/codec/dynamic-range 重建徽章，直接违背了 query.md 推荐的统一 `MediaBadge` 形状。
- 详情模板重复计算 ViewModel 已给出的 season active/name，并自行计算 episode active；这不是数据库业务逻辑，但说明 Jinja 尚未完全达到“只画最终页面形状”。

## query.md 合规结论

- 通过：轻量目录、无 ORM/Repository/Service、薄 `db.py`、统一 `/catalog`、统一 `/title/<title_id>`、POST `/play/<file_id>`、首页页面级 query/ViewModel、SQL 下推筛选、最近观看单 SQL/CTE、卡片宏复用。
- 部分通过：大多数读取 route 简短清楚，但 collection 未封装页面级 query，manager 在 ViewModel 后继续整形；Jinja 仍有部分 active/name/图片首选判断。
- 未通过：`selected_episode` 与 `resume_episode` 未分离；统一 MediaBadge 虽有函数却未被 UI 消费。

## 优先级建议

1. 轮换并移出源码中的凭据，同时补 `.env.example` 与启动时缺失配置错误。
2. 修复 series resume 语义与已完成电影播放文案，并补回归测试。
3. 将多 artwork 保存收口为一个事务。
4. 明确收藏 episode 的卡片标题/副标题，避免同剧多集不可辨认。
5. 再清理 title ViewModel 兼容适配、badge 重复和 manager 双重筛选。
