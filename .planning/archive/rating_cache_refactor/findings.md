# Findings

- 用户明确不处理源码内 OMDb API Key。
- 修复范围为此前审查发现的 1、3、4：缓存语义、异常边界、重复逻辑。
- 既有项目边界要求合并真正共享逻辑，但避免巨型分支和机械拆分。
- 当前媒体缓存仅判断时间戳是否存在；季缓存只判断任意一集时间戳，并在保存时给未返回单集补写时间戳。
- `get_media_external_ids()` 吞掉 TMDB 请求失败；app.py 三处捕获 `Exception`，会一并隐藏数据库错误。
- 两个 OMDb 方法重复密钥解析、HTTP/JSON处理、错误分类；电影和剧集重复检查/抓取/保存编排。
- 已用 `_fetch_omdb_payload()` 合并 OMDb 访问，用 `_sync_media_ratings()` / `_sync_season_episode_ratings()` 保持 app.py 的编排职责。
- 媒体评分缓存现在有 30 天 TTL；部分刷新使用 COALESCE 保留未返回的旧分数。季缓存要求所有本地单集都有新鲜时间戳，不再为 OMDb 未返回的单集伪造完成状态。
- app.py 只捕获 `RatingFetchError`；SQLite 等持久化错误继续抛出。
