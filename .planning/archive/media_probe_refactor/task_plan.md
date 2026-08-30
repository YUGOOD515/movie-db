# 详情编排与媒体参数

## 目标

拆分详情页编排、剧集只渲染当前季、修正 `source_order` 语义，并通过 ffprobe 获取和持久化真实媒体参数。

## 阶段

- [complete] 1. 审计详情数据流、source_order 使用点、来源表和 ffprobe 环境
- [complete] 2. 拆分 render_media_detail 并实现当前季渲染
- [complete] 3. 将 source_order 更名为 source_id 并回归排序与播放链接
- [complete] 4. 设计并起草媒体参数迁移 SQL，等待明确批准
- [complete] 5. 实现 helpers ffprobe、models 持久化、导入/回填与页面展示
- [complete] 6. 执行获批迁移、回填现有来源并完成验证

## 决策

- `helpers.py` 负责 ffprobe 子进程与 JSON 解析；`models.py` 负责 SQLite；`app.py` 只编排。
- 数据库迁移在用户审阅 SQL 并明确批准前不执行。
- 不修改现有 `.gitignore` 用户改动。
- 媒体参数直接保存到电影/单集来源表，字段统一，避免无外键约束的多态参数表。

## 错误记录

- PowerShell 预检命令中的嵌套双引号未闭合；已拆分命令后重试，不影响文件与数据库。
- PowerShell 将 Python f-string 的格式冒号误解析为自身语法；迁移未开始，改用单引号包裹 Python 参数后重试。
- PowerShell 向 `python -c` 传参时再次剥离内部引号；迁移仍未开始，改用原生 SQLite CLI 和 PowerShell 文件复制。
- 首条真实来源回填时 `127.0.0.1:5244` 拒绝连接；AList 未运行，未写入任何错误元数据，先定位原有启动方式。
