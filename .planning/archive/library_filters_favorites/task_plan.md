# 收藏与类别筛选

## 目标

完成电影/剧集类别筛选页、主页剧集类别板块，并为收藏功能准备独立于 TMDB `collection` 的数据库结构。

## 阶段

- [complete] 1. 核对现有关系表、查询和模板结构
- [complete] 2. 起草收藏数据库迁移 SQL，等待用户批准，不执行
- [complete] 3. 实现电影/剧集统一筛选查询与筛选页面
- [complete] 4. 主页增加剧集类别，类别卡补充图标
- [complete] 5. 测试类别功能并汇报数据库审批关卡
- [complete] 6. 获批后执行迁移、验证数据库并实现收藏页面及收藏交互

## 决策

- `collection` 保留 TMDB 电影合集语义；用户收藏使用新的 `favorite_*` 表。
- 类别筛选使用现有 genre、production country、spoken language、日期和评分数据，不改表。
- 用户已明确批准；`005_add_favorites.sql` 已执行并通过完整性检查。
- 收藏采用 `favorite_folder` + `favorite_item`，后者通过互斥的电影/剧集外键保留 SQLite 引用完整性。
- 筛选结果默认按最近来源顺序排列；电影和剧集使用同一竖版海报宏。
- 实际 `movie.db` 已创建 `favorite_folder` 和 `favorite_item`，并写入三个系统收藏夹。
- 收藏详情操作仅管理电影/电视剧系统收藏夹；自定义收藏夹的数据结构保留，管理界面不在本次范围内。

## 错误记录

- `rg schema.sql` 误用了仓库根路径；实际完整结构位于 `migrations/schema.sql`。
- PowerShell 将内嵌 Python SQL 的 `<>` 解析为重定向；改用 here-string 通过标准输入运行只读脚本。
- 只读脚本输出语言名称时命中 Windows GBK 编码限制；后续命令显式设置 `PYTHONUTF8=1`。
- 组合补丁不能对同一路径同时执行 Delete/Add；业务文件未改，改为先更新代码再单独替换模板。
- 技能 `check-complete.ps1` 未识别 scoped plan，显示 0/0；以计划文件中 6/6 complete 和实际测试为准。
- 最终组合 `rg` 行号检索再次被 PowerShell 拆分；改用分文件简单表达式，不影响实现。
