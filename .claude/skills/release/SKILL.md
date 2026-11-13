---
name: release
description: 发布新版本：根据上次发版以来的提交汇总 CHANGELOG，检查文档是否过时，并更新版本号。
disable-model-invocation: true
argument-hint: "[新版本号，如 0.8.0；留空时按改动内容推断]"
---

按 `docs/zh-CN/development.md` 的“持续集成与发布”一节执行，步骤以那里为准。补充要求：

- 上一版本取 `git tag --sort=-v:refname | head -1`。新版本号用 $ARGUMENTS；留空时按文档规则推断，并在动手前告诉我推断结果。
- CHANGELOG 沿用已有条目的写法：中文，按主题分组，每条一句话说清改了什么和对用户的影响。合并提交和只改测试、开发文档的提交跳过。
- 检查文档时，把发现的过时描述连同文件和行号列给我，改完再继续。
- 合并到 `main` 和推送之前停下来，把 CHANGELOG 新增内容和版本号给我确认。
