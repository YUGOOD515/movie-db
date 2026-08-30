# library.db 建库计划

## 目标

依据 `本地影视库数据库数据字典.md` 新建空数据库 `library.db`。不修改 `movie.db`，不迁移或预置任何业务数据，不切换现有应用连接。

## 阶段

- [x] 核对数据字典、工作区状态与 `movie.db` 基线
- [x] 编写可复现的独立建库脚本
- [x] 执行建库
- [x] 校验表、字段、约束、索引、空数据与数据库完整性
- [x] 复核 `movie.db` 未变化

## 决策

- 建库脚本保存为 `migrations/library_schema.sql`，不覆盖现有 `migrations/schema.sql`。
- 只建立数据字典中的 7 张业务表，不插入种子数据。
- 使用数据字典规定的主键、唯一约束、外键和枚举语义；字段名保留为 `collection."order"`。
- 本阶段不修改 `app.py`、`models.py` 或其他运行时代码。

## 错误记录

暂无。

## 结果

- `library.db` 已建立，包含 7 张业务表和 4 个显式索引。
- 7 张业务表均为 0 行；未迁移或预置业务数据。
- `PRAGMA integrity_check` 返回 `ok`，`PRAGMA foreign_key_check` 返回 0 条违规。
- `movie.db` 创建前后 SHA-256 均为 `581B8FB3D855FF89351E5A81DB9E9A2EB9D88A7C39CFA60396F524B183BF32E9`。
