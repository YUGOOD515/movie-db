# imdb.db 替代评估

日期：2026-09-26。只读检查当前工作区和数据库，进行 13 次影视 API GET 请求；未修改应用代码、数据库或图片。原始响应见同目录 probe_results.json，不含请求凭据。结论只覆盖本次样本，不代表全库覆盖率。

## 结论

最简目标链路是 TMDB 提供基本资料、演员、图片、季集结构与 IMDb ID，MDBList 提供评分；统一转换后写入现有 library.db。继续以 IMDb ID 为主键，网页继续只查 library.db。无须改为实时 API 页面，也无须迁移播放进度、收藏和图片目录。

但按“保留现有全部功能，包括新增单集的 IMDb 评分”衡量，当前账号尚不能完成纯 API 替代。MDBList 单集评分矩阵未返回逐集数据，旧接口直接查询单集也返回 Not Found。保留现有评分只能保护历史数据，不能解决新增单集。

## 当前真实边界

- imdb.db：5,224,140,800 字节，约 5.22 GB / 4.87 GiB。
- library.db：1,105,920 字节；16 部电影、23 部剧集、536 条单集。其中 484 条单集有 IMDb 评分，527 条单集已有 TMDB ID。
- app.py 的 IMDB_DATABASE 只传给单条/批量录入。浏览、排序、筛选、详情、收藏、播放读取 library.db。
- queries/imdb.py 提供基本字段、中文别名、IMDb 评分和季集关系。
- services/media_import.py 用 IMDb 结果建立全部单集，再按季集位置合并 TMDB 补充信息。
- connectors/tmdb.py 也直接读取 imdb.db，解析父剧 IMDb ID、单集 IMDb ID。
- 当前 scripts/normalize_alist_names.py 使用 GuessIt 和 library.db，不依赖 imdb.db；不能把以前规划中的匹配能力算作当前已实现功能。

## 实测

| 请求 | 结果 |
|---|---|
| TMDB find tt0816692 + movie/157336 | 200，星际穿越，基础信息、external_ids、credits、images 可取 |
| TMDB tv/95396 | 200，Severance，父剧 IMDb ID 与聚合演员可取 |
| TMDB find tt15180436 | 200，返回 show_id=95396、S2E1；支持单集 IMDb ID 输入 |
| TMDB S2E1 detail + external_ids | 200，返回 tt15180436、时长、简介等 |
| TMDB season/1 + episode/1/external_ids 等 | 200，但忽略附加单集 ID；不能用这一入口批量获取 |
| TMDB 整剧详情 + season/1,season/2,season/2/episode/1/external_ids | 200，季列表和嵌套单集 ID 均返回 |
| TMDB 整剧详情一次 append 19 个单集 external_ids | 19/19 返回 IMDb ID；本地可比 10 条全部一致 |
| MDBList 新版电影接口 | 200，IMDb 8.7、2,611,246 票，另有多平台评分 |
| MDBList 新版剧集接口 + episode_ratings | 200，seasons=[]，只有 19 集汇总和 overall_average=83 |
| MDBList 新版 /imdb/any/tt15180436 | 400，Invalid media_type；文档列出的 any 在这次请求未被接受 |
| MDBList 旧版按单集 tt15180436 查询 | HTTP 200，但 response=false / Not Found |
| MDBList 旧版电影查询 | 200，继续支持当前解析结构 |

13 次内容请求耗时约 5.0–6.5 秒/次，包含本次网络路径开销，不是性能基准。未下载图片，未调用 AList、ffprobe 或播放接口。

MDBList 的公开 OpenAPI 文档说明：episode_ratings 的逐季逐集矩阵为 Supporter 功能，普通响应只有汇总。当前凭据的响应属于汇总形式；未读取账号订阅状态，未付费，也未验证付费矩阵的评分来源、量纲和覆盖率。不能将 overall_average 或 TMDB vote_average 当作 IMDb 单集分数。

## 建议链路

```text
IMDb ID / 剧集 ID + 季集号
    → TMDB find（确定 movie / tv / episode，已有可靠映射时可省）
    → TMDB detail + images + credits/aggregate_credits + external_ids
    → 剧集：批量季详情 → 批量单集 external_ids
    + MDBList 电影/整剧评分
    → 单一 bundle（titles / relations / artwork / cast / ratings）
    + AList、ffprobe 的文件信息
    → 现有事务写入 library.db
    → queries → viewmodels → Jinja
```

TMDB 整剧请求批量单集 ID 的可用格式：

```text
GET /3/tv/95396
append_to_response=season/1/episode/1/external_ids,season/1/episode/2/external_ids,...
```

官方 append 上限为 20 项，按 20 分块；如果同一请求追加其他资源，也占用名额。保留批次内剧集上下文和已有事务回滚，不引入新持久任务系统或额外数据库。

冷录入、分开取季与 ID、忽略图片请求时：电影约 3 次元数据请求；剧集约 3 + ceil(S/20) + ceil(E/20) 次，包含 find、详情、MDBList 各 1 次。19 集两季约 5 次，当前链路约 4 次。联网次数不会因删除本地库自然减少；合并已知季号或复用已有映射后可进一步减少。

建议继续复用现有 MDBList 旧接口来减少改动，先增加 IMDb 评分字段映射。新版接口可另外升级，但需要适配 ids 嵌套结构、popcorn 与旧 tomatoesaudience 的差异，不能仅替换 URL。

## 功能等价的必要条件

1. 基础字段改从 TMDB 生成，并扩展现有 build_title_supplement/build_episode，而不是继续保留“IMDb 主资料 + TMDB 补充资料”的双份合并。
2. extract_rating_fields 当前没有把 IMDb 评分写入 average_rating/num_votes，必须增加；models/library.py 的 RATING_COLUMNS 白名单同样没有这两个字段，须同步增加。评分请求失败/缺失时保留旧值，新增数据允许空值，不能伪造为 TMDB 分数。
3. 中文标题采用 TMDB zh-CN，再回退原名；这与 IMDb CN/TW/HK 别名优先规则不是相同数据。Severance 在本次 zh-CN 整剧响应仍为英文。
4. 分类不能直接写入中文 TMDB genres.name：现有库按 IMDb 英文分类筛选。电影/电视需显式规范化；例如 Sci-Fi & Fantasy 与 IMDb Sci-Fi/Fantasy 不是一一对应。若要求分类结果完全一致，TMDB 无法直接保证。
5. last_air_date 不是停播年份；仅在适当的终结状态下生成 end_year。单集年份取 air_date，单集 runtime 缺失保持空值。
6. 保留现有 title_id、关系和历史评分；缺失 TMDB ID 的 9 条现有单集不可丢弃。这 9 条属于 tt14688458 的 S4E2–E10，未进一步验证当前 API 是否已补全。
7. 不按同一季集位置假设 IMDb 与 TMDB 必然对应；本次 Severance 可比样本一致，不代表动漫、特别篇、重编季全部一致。遇到冲突保留已有关系并报告。
8. 若要收录 API 没有 IMDb ID 的新单集，当前 title_id 的 tt 格式约束无法表示它；需要另行设计内部 ID，这会扩大迁移范围，不属于本次最简方案。

## 代码量：实测基线与设计估算分开

统计口径为 Python 物理行数，包含空行/注释；当前运行时代码（app.py + connectors/models/queries/services/viewmodels）共 5,713 行。未实施重构，所以以下净变化不是已验证 diff。

可明确移除的部分：queries/imdb.py 128 行；TMDB 中两个 SQLite 映射函数共 30 行；connect_imdb 3 行；另有少量配置、导入和数据库上下文参数。scripts/import_imdb.py 的 361 行属于离线维护脚本，不能混入运行时节省。

估算账本：

| 项目 | 预计物理行变化 |
|---|---:|
| 移除 IMDb 查询、连接、配置与相关传参 | -180 至 -210 |
| 合并原有双来源组装、目标解析，移除重复分支 | -30 至 -50 |
| TMDB 完整字段转换、20 项分批、父子 ID 与缺失处理 | +140 至 +200 |
| 评分写入、分类/年份转换及失败保留旧值 | +20 至 +60 |
| 运行时净变化 | 约 -100 至 +50 |
| 再退出 IMDb 导入脚本 | 额外 -361 |
| 包含维护脚本的总净变化 | 约 -461 至 -311 |

这套估算对应上述 TMDB + MDBList 主链路，尚不包含一个未验证的单集 IMDb 评分补充来源，也不包含测试、文档、旧库清理或主键迁移。不能把它称为严格功能等价方案的最终代码量。若需额外抓取/兜底来源，净减少可能消失。

因此主要收益是退出约 5.22 GB 的全量主数据与 TSV 导入维护，运行时代码预计只是小幅变动。不要为了“减几百行”混淆维护脚本和业务代码。

## 决策

当前不建议直接删除 imdb.db。最简目标架构已经可行，单集身份映射也实测通过；完全替代的阻塞点是新增单集 IMDb 评分及数据覆盖差异。若严格保留它们，先保留现有库作为该能力的来源；若未来取得并验证合适的单集评分接口，才可以关闭本地库依赖。若接受新增单集评分暂空、保留历史评分，则可直接采用该 API 链路，但这属于明确的功能降级。

来源：
- https://developer.themoviedb.org/reference/find-by-id
- https://developer.themoviedb.org/reference/tv-series-details
- https://developer.themoviedb.org/reference/tv-episode-external-ids
- https://developer.themoviedb.org/docs/append-to-response
- https://api.mdblist.com/docs/
- https://api.mdblist.com/schema/ （本地 requests 已读取 JSON）
- https://docs.mdblist.com/docs/api
