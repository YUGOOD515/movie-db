# Findings

- 当前 `render_media_detail()` 同时承担来源选择、单集组装、展示标签和外链生成，需要拆分。
- `source_order` 当前多处实际承载 `source_id`，需要完整追踪后统一命名。
- ffprobe 持久化会改变数据库结构，必须先提供迁移 SQL 审阅。
- 系统已安装 `C:\Program Files\ffmpeg\bin\ffprobe.exe`，版本为 2026-02-04 构建。
- `source_order` 共出现在首页/筛选/收藏/资源概览查询与 `add_media_routes()`，均实际是来源主键或其 MAX 值。
- 剧集详情 SQL 当前一次读取全部季和全部单集来源；应先确定 featured source，再将模板数据裁剪为当前季。
- 来源写入集中在 `_upsert_source()`，电影和剧集导入最终都经过该函数，适合统一写入 probe 参数。
- `.gitignore` 是任务开始前的用户未提交改动，保持不变。
- 旧“下一来源”逻辑按来源列表下标前进，会把同集另一版本误当下一集；重构后改为按季/集坐标选择下一集首个来源。
- AList `get_video_link()` 返回可供 ffprobe 直接读取的 raw URL；只保存探测结果，不保存临时 URL。
- 迁移草案 `006_add_source_media_metadata.sql` 为两种来源表增加相同的容器、时长、大小、码率、视频、音频和探测时间字段。
- 历史来源 728 条中 727 条可由 ffprobe 正常读取；`tv_episode_source.source_id = 435` 对应《鱿鱼游戏》S02E02《万圣节派对》，源 MP4 的顶层结构为 `ftyp`、`free`、延伸至 EOF 的 `mdat`，缺少 `moov`，需要替换或修复源文件后再回填。
