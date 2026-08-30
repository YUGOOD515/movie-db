# 当前代码质量与 query.md 合规审查

## 目标

只读审查当前项目，识别冗余代码、不当逻辑、职责越界和测试缺口，并逐项对照 `.planning/rebuild_route_query/query.md`。

## 阶段

- [x] 1. 读取项目约束、query.md、历史背景与工作区状态
- [x] 2. 审查 app.py、db.py、queries、viewmodels 的职责与数据流
- [x] 3. 审查 Jinja、静态脚本、models/helpers 的重复与越界
- [x] 4. 运行静态检查、测试与数据库只读验证
- [x] 5. 汇总按严重度排序的发现与合规结论

## 边界

- 不修改应用代码、模板、数据库或原有规划文件。
- 工作区已有 `templates/_media_cards.html` 修改，视为用户改动，不覆盖。
- 结论以当前工作树为准，Git 基线仅用于辨认改动范围。

## 错误记录

- 首次直接在 `app.app_context()` 中构建 ViewModel 时，Flask `url_for()` 因无请求上下文报错；改用 `app.test_request_context()` 重试。该错误属于审查脚本上下文不足，不是应用运行错误。
- 两条 `rg` 组合正则受 PowerShell/JSON 引号影响成为未闭合表达式；改用多个固定字符串 `-F -e` 检索，不重复原命令。
- 一次包含反引号和中文空格的多模式 `rg` 锚点命令仍被 PowerShell 拆参；改用 Python 逐行固定字符串匹配。
- 项目虚拟环境未安装 Ruff，因此不把 Ruff 结果作为验证门；改用 compileall、AST 结构审计、单元测试、Node 语法检查和 diff 检查。
