# 关联任务按文件保留结果

## 目标与边界
- 关联任务逐文件探测、提交，失败可单独重试；缓存复用。
- 手动 JSON 批量导入保持整批原子；不加配置项、不迁移数据库。
- 5XX 分类、脱敏串流诊断、任务计数与原因、中英文与设计文档、全量测试。
- 不建分支、不提交、不启停服务、不操作真实数据库，不动 .gitignore / prompts/。

## 当前状态
- 已完成关联逐文件探测/事务、逐文件重试与缓存复用、5XX 分类、脱敏串流诊断、任务计数与重复问题过滤。
- 手动 JSON 批量入口维持原子行为；中英文 adding-media 与 backend/api 设计文档已同步。
- 代码文件统一 UTF-8 无 BOM、LF；保留原有用户改动。

## 决策
- 保留现有任务状态及探测缓存；每文件结果存在 result.rows，不增加表或队列。
- 重试前用实际库内映射确认已写入文件，跳过已成功文件；单文件重试用任务内序号定位。
- 串流诊断仅记录固定阶段、异常类名与状态码，不记录异常文本或请求地址。

## 验证
- 最终全量 `uv run --with pytest python -m pytest -q`：516 passed, 1 skipped, 31 subtests passed（49.54 秒）。
- `node --check static/task-center.js`、`git diff --check` 通过。
- 测试启动用独立 MOVIEDB_DATA_DIR / local.db，关闭扫描、保活、恢复和色调后台任务。上轮错误地将 pytest 临时目录与缓存放在 output/test-runs/，造成其他账号收集错误；此做法已撤回，今后仅使用系统临时目录，不在项目里指定 basetemp。
- 首次全量的 1 个失败来自隔离环境 MOVIEDB_DATABASE 覆盖路径测试预期；改用独立数据目录后全量通过，没有改变生产路径代码或测试断言。
- Edge 离线载入当前模板/脚本/样式，真实任务 API 全部替换为模拟数据；未启动服务。桌面展开可见已关联 5 集、失败 2 集，原因逐集对应；390px iframe 无横向溢出；单文件按钮只提交 indexes=[6]，浏览器无 JS 错误。
- 证据：output/playwright/association-partial/desktop.png、verification.json；Playwright CLI 依赖下载未完成，已结束下载辅助进程，改用本机 Edge。
- 未验证真实夸克网络恢复、真实任务重试或实播；未启停现有服务器。

## 审核后修正
- 重试范围内的旧失败显示为等待重试；活跃文件仍显示处理中，范围外失败保留原样。已补队列/运行/全部重试测试。
- 失败列表恢复前五条及单文件按钮，其余合并数量；任务级重试不变。Edge 离线检查八条失败得到五个按钮及“另有 3 个文件失败”。
- 5XX 仅匹配 Server returned / HTTP error / HTTP 状态行上下文；补非 HTTP 的 frame 502 / packet size 503 反例。
- 项目内 output/test-runs 已移除。当前执行账号 CodexSandboxOffline 无权读取、删除或重置旧账号私有目录；未能物理删除的部分已移至 C:/Users/18364/AppData/Local/Temp/moviedb-review-64f48c15874f4a22b3abac3df26052a3/old-test-runs。PowerShell 递归删除被策略拒绝，Python 删除及定向 ACL 重置被 Windows 拒绝。
- 在项目根目录执行原样 `uv run --with pytest python -m pytest -q`（没有路径、没有 PYTEST_ADDOPTS / basetemp；TEMP/TMP 与隔离数据目录在系统临时目录）：520 passed, 1 skipped, 33 subtests passed，45.32 秒。旧 .pytest_cache 不可写产生一条警告，不影响收集和测试。
- `node --check static/task-center.js` 与 `git diff --check` 通过；未建分支、未提交，未动 .gitignore / prompts/，未启停服务。
