# 悬浮岛“忽略 / 重试”按钮晚出现：四个执行者对比

由 Claude 指挥，Codex 与 AntiGravity 依次执行（不开工作树，同一工作区轮流跑，每轮结束存补丁并恢复干净）。最终采用 sol 的方案，提交 `acada61`。

## 根因

点开失败卡片时 `toggleTask()` 开始第一段高度动画；逐集详情很快返回，`loadDetail()` 再调用 `resize()`。第二次量 `offsetHeight` 时第一段动画还在覆盖高度，量到的是中间值，第二段动画因起止相同被跳过或目标偏小。第一段播完后卡片才跳回真实高度（演示页实测：动画目标 146px，真实 200px），按钮正好在被 `overflow: hidden` 裁掉的那 54px 里。

修法：`resize()` 先记下可见高度、取消上一段高度动画，再量真实高度并过渡；被取消动画的 `finished` 会 reject，需要处理。

## 各执行者结果

| 执行者 | 轮数 | 结论 | 用量 / 耗时 |
|---|---|---|---|
| Codex gpt-6-luna（max） | 2 | 首轮只看到动画裁切，去掉了展开动画（掩盖问题）；次轮根因对了，但按关键帧扫描取消所有含 height 的动画，会误伤 `leave()` 的收起动画，且 `loadDetail` 的 reject 未处理 | 次轮输入约 908 万 / 输出 10 万，最贵 |
| Codex gpt-6.1-sol（high） | 1 | 根因与改法正确，在 `resize()` 内统一吞掉 reject；自己在 Edge 里做了前后对比。扣分：擅自尝试建分支，留下 npm-cache | 输入约 119 万 / 输出 0.8 万 |
| Codex gpt-6.1-terra（high） | 1 | 根因与改法正确，reject 在调用处处理；未做浏览器验证 | 输入约 33 万 / 输出 0.26 万，最省 |
| AntiGravity Gemini 3.8 Flash High | 2 | 根因分析最详细；首轮漏了 `loadDetail` 的 reject（实测快速收起会出 1 个 unhandledrejection），次轮补上后与 terra 相同 | 每轮 16–18 分钟，输出约 9–10 万 |

## 流程经验

- `run.sh` 新起一轮、`resume.sh` 接着原对话返工；只 stash 代码目录，不要 stash 整个仓库（用户可能同时在改 `prompts/`、`.gitignore`）。
- 统一用同一份 `prompt.md`，返工时只给观察到的现象和数据，不直接给改法，便于比较各模型。
- 审核时用 `scripts/task_center_demo.py` 复现，并用数据（动画目标高度、unhandledrejection 计数）而不是肉眼判断。
