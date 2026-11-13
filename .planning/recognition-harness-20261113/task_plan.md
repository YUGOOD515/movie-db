# 多层目录识别与 AI harness

## 目标与验收

沿用 `codex/alist-stream-prefetch`，保留工作区已有改动。修复分类目录被当作一部作品；为 AI 提供目录上下文、只读补查、结构与业务校验、有限纠错、可回放记录。识别只产出建议，不自动入库。

验收：当前阿里 `/阿里/WJ/video/Special` 的 105 个文件均有去向、无重复，作品/国家版本/电影与剧集分别处理；同作品季与画质上下文保留。离线回归通过，真实目录回放和有限真实 AI 验证如实记录。

## 当前状态

- 已确认旧逻辑将 Special 的 105 个视频合并；规则分数 0.793，覆盖率 0.467，因分数大于 0.75 未升级给 AI。
- 已实现按作品划分、AI 快照分组、只读工具、业务校验后纠错、模型/提示词缓存指纹和回放脚本。
- 已实现缺少单集 IMDb ID 的明确提示，不为这类不可修复的数据缺失浪费 AI 重试。
- 已完成快照离线/真实 AI 回放、全套测试、实际页面与完整扫描验收。应用已恢复在 127.0.0.1:5000，保持调试模式。
- 本轮已完成并单独提交在当前分支；其它工作区变更保留，未推送或合并主分支。最终验收汇总在 `local-test-data/recognition-harness/report-final.md`。

## 设计决定

- 继续使用项目现有 DeepSeek 配置与 LLM connector，不引入新的 AI 服务或凭据。
- 规则负责枚举与简单分组；AI 可提出分组与补查，程序执行只读工具并校验文件编号的完整性/唯一性。
- 重试受请求、工具、输入/输出和总耗时预算约束；无法确定时保留待核对。
- 模型候选仍需 TMDB/IMDb 及季集校验；校验反馈可触发有限修正。
- 运行快照、日志与评测报告放在被忽略的 `local-test-data/recognition-harness/`；不包含凭据或签名链接。

## 参考

- MoviePilot transfer-failed-retry：只读核验、按已证明的身份分组、保留预算与证据。
- media_organizer_rs core/metadata.rs：区分分类/作品/季/画质目录。
- PydanticAI output validator / ModelRetry；LangGraph evaluator-optimizer；promptfoo 自定义断言。

## 验证记录

- 最终 Python 全套：465 passed，1 skipped，31 subtests passed（38.56 秒）。前端规则与关联脚本语法检查通过。
- Special 105 个文件 → 13 组，独立目录标签对照误合并/误拆分对数均为 0，无遗漏无重复。
- live-01 暴露韩文原名比较与缺少单集 IMDb ID 的问题，已修正；live-02 13 组全部有作品身份，3 ready / 10 review，识别阶段 4 次 AI 请求（2581 输入 / 176 输出 token），分组阶段另计。review 主要因 TMDB 单集未提供 IMDb，SKAM 另有分片/预告片集号需核对。
- 报告：`local-test-data/recognition-harness/live-02/report.md`、`result.json`；标签对照 `label-checks.json`，离线最终回放 `offline-final/`。
- `scripts/check_env.py` 提示本机 `.env` 原有 `FFPROBE_PATH` 未设置；本轮未新增环境变量，未修改该配置。
- 开发中暂停原调试进程，恢复时 Start-Process 被策略拒绝，改用受控终端会话按原端口/调试模式启动。
- 实际完整扫描 status=complete，无警告；Special 仍为 105 文件 / 13 组，3 ready / 10 review，没有未识别作品。实际关联页已查看分组、季/画质版本和缺少 IMDb 的中文提示。
- 全部扫描文件对照原快照 301 个，无遗漏、无多出、无重复。风光素材原合集 72 个文件保留为非影视合集，可手动关联。
- 实际模型曾把 Tiny World 标为 other，已增加剧集标记与库内身份优先的硬校验；补查失败缓存只降低重试标记，仍显示已有结果，后续扫描可重试。
