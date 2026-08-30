# Findings

- `movie_image` 与 `tv_series_image` 以 `(媒体 ID, image_type, image_position)` 为主键，每类最多 5 张；全站查询均按 `image_position` 取第一张，因此交换位置即可统一切换首选图。
- 管理页概览查询已返回三类首选路径，但当前 `overview_card` 只把横板图作为主图并叠加 Logo，没有展示独立竖版海报，也没有返回图片候选集合。
- 图片类型数据库值为 `backdrops`、`posters`、`logos`；展示语义分别对应横板海报、竖版海报、Logo。
- `static/style.css`、`.gitignore` 及两个 Dolby Vision 图片在本任务开始前已有用户改动或未跟踪文件；只在确有必要时对 CSS 做局部追加，不触碰其他文件。
- 用户选择通过交换数据库中第 1 位与目标图片的数据实现；重新录入媒体时按原首选 `local_path` 重新排序，因此选择可以跨重复录入保留。
- 实际媒体库共 31 部作品，三类当前图片均完整；管理页输出 394 个候选操作，读取页面不会改变实际数据库选择。
