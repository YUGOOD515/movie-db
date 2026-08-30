# 代码质量审查问题全量修复

## 目标

修复 `.planning/code_quality_audit/findings.md` 中全部可在仓库内完成的问题，并落实用户补充的续播与收藏规则。

## 规则

- 剧集最近观看记录达到 90%，或剩余时长少于 2 分钟：续播下一集；否则续播该集当前位置。
- 收藏对象只允许 movie/series；episode 自动归一到所属 series，mutation 层拒绝 episode。
- 不修改正式 `library.db`，mutation 测试仅操作临时副本。
- 保留用户对 `templates/_media_cards.html` 类别卡片的现有修改。

## 阶段

- [x] 1. 恢复上下文并确认工作区、审查发现与用户规则
- [x] 2. 配置安全：移除硬编码凭据并补环境配置说明
- [x] 3. 业务逻辑：续播、收藏归一、完成状态与 artwork 原子事务
- [x] 4. 分层与冗余：页面级 query、manager、ViewModel/Jinja/徽章清理
- [x] 5. 前端遗留：新详情 URL、图片 fallback、筛选一致性
- [x] 6. 测试：自包含 fixture、语义回归、静态与数据库验证
- [x] 7. 纠正最近观看入口：历史展示保持最新记录，详情默认选中与播放目标统一为续播单集
- [x] 8. 修复 Top 3 横版卡片：左下仅 Logo，年份与评分右对齐，修正蒙版层级并完成浏览器视觉验证
- [x] 9. 详情页信息区微调：作品信息与当前播放文件纵向排列，外部链接移动到页面末尾

## 错误记录

- 首次合并修改 `models.py` 与 `app.py` 的补丁因 `models.py` 尾部上下文不精确而整体未应用；已确认没有产生部分修改，改为按函数边界拆分补丁。
- 凭据残留检查的复杂 `rg` 正则再次受 PowerShell 引号影响；Python 编译已通过，残留检查改用简单固定字符串与 Python AST/文本检查。
- 详情模板 active/name 清理补丁因按钮内部多一层 `<span>` 导致上下文未匹配；没有产生部分修改，已按实际行内容拆分重试。
- 审查记录引用了不存在的 `summary.md`；实际审查结论完整保存在 `findings.md`，不影响修复范围。
- 最终定位行号时组合 `rg` 正则在 PowerShell 路径反斜杠处解析失败；没有修改文件，改用固定字符串分别查询。
- 固定字符串查询中的嵌套双引号仍被 PowerShell 拆分；无文件影响，停止复杂 shell quoting，已有函数行号足够交付。
- Playwright CLI 首次打开本地页面失败：默认查找的 Chrome 路径不存在，并提示安装 Chrome distribution。未安装额外浏览器；下一步先检查 CLI/browser 参数与现有 Edge/Chromium，可用现有浏览器完成验证。
- `playwright-cli open --help` 正常列出 `--browser msedge` 后在 Windows 退出阶段出现 libuv assertion；帮助内容已取得，现有 Edge 路径确认存在，后续使用命名 session 与 msedge，避免重复默认 Chrome 路径。
- Top 3 修改后的首轮测试有两项失败：fixture 实际只有 2 部满足 Top 条件，结构断言错误写死 3；续播文案意外从带季集号退回仅时间。前者应按 fixture 数据修正，后者保留严格断言并诊断根因。
- 第二轮测试仍有两项失败：并发文案修改也把完成电影标签从“播放电影”简化为“播放”；Top fixture 的第二部合格电影缺少 Logo。前者改为验证“不显示继续播放”的业务语义，后者补齐 fixture Logo，使结构测试与当前正式库前 3 均有 Logo的事实一致。
- 浏览器首次 reload 后 snapshot 仍显示旧标题结构；原因是当前 Flask 非 debug 服务缓存了已加载的 Jinja 模板。需重启本地验证服务后再截图，不据旧 snapshot 判断修复结果。
- 重启服务后旧 Playwright session reload 到 `about:blank`，说明之前 Edge 会话已失效；已用同名 session 重新打开 `/top`，不复用空白页状态。
- 最终定位链接行号时 `rg -F` 的嵌套双引号再次被 PowerShell 拆分；不影响文件和验证结果，改用已知详情区入口行交付。
