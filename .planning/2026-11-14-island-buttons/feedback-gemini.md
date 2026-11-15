根因判断正确，核心改法也对，但还有一个问题需要修：

`resize()` 现在会取消上一段高度动画，被取消的动画的 `finished` Promise 会 reject（AbortError）。`toggleTask` 那里有 `.then(reveal, () => {})` 兜住，但 `loadDetail` 里 `resize(render)` 返回的 Promise 没人处理。我在演示页上实测：点开失败卡片，不等动画播完再点一下收起，控制台会出现 1 个 unhandledrejection。

请用最小改动修掉这个问题，不要动其他逻辑；改完跑 `node --check static/task-center.js`。不需要再跑完整的 pytest。最后用中文简短说明改了哪里。
