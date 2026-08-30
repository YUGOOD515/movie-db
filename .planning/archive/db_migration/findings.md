# 建库发现

- `library.db` 在执行前不存在。
- `movie.db` 执行前大小为 839680 字节，最后修改时间为 2026-08-29 20:45:03；最终将再次计算 SHA-256 并比较。
- 数据字典定义 7 张业务表：`title_basic`、`title_relation`、`title_artwork`、`media_file`、`playback_progress`、`collection`、`collection_item`。
- 数据字典明确要求 IMDb `tconst` 作为 `title_id`，本地类型统一为 `movie`、`series`、`episode`；图片类型统一为 `poster`、`landscape_poster`、`logo`、`still`。
- `playback_progress.playback_id` 使用 `AUTOINCREMENT`，因此 SQLite 会自动创建内部表 `sqlite_sequence`；它不计入 7 张业务表。
- 本任务明确不迁移数据，并免除 SQL 执行前审核。
- 建库结果为 7 张业务表、4 个显式索引和 7 条外键；SQLite 内部另有 `sqlite_sequence`。
- `library.db` 建库后大小为 90112 字节，SHA-256 为 `4D118B7E1BCD961FF2F716FEF62152D5BEED0427AD085E7ACCDCBC843C2B1E9A`。
- `movie.db` 建库前后 SHA-256 均为 `581B8FB3D855FF89351E5A81DB9E9A2EB9D88A7C39CFA60396F524B183BF32E9`。
