# Findings

- 当前 Hero 的主播放按钮与版本 Dropdown 分离；单集中央按钮直接播放，版本 Dropdown 位于卡片文字区。
- 季摘要位于每个 `season-pane` 的首个 `div.row`，与上方季 Tabs 分离，形成重复层级。
- 单集目前使用 Bootstrap `.row` 与 `.col-lg-3` 网格，没有横向溢出。
- `episode.sources` 和 Hero 的 `hero_versions` 已含全部版本及播放 URL，无需修改 Model 或数据库。
- 强制圆形按钮出现在 layout 菜单键、首页播放键和详情单集播放键；品牌圆形标记不是按钮，不在本次范围。

## 验证结论

- 生产库多版本电影 969681 的 Hero 原播放键渲染为 Dropdown。
- 生产库多版本单集 1318082 的中央播放键渲染为 Dropdown，所属剧集详情返回 200。
- 每个 season pane 的首个元素为 `.episode-rail`；季摘要位于 `.season-tab-overview`。
- 页面不再渲染独立“播放版本”标题，强制 `rounded-circle` 仅剩非按钮品牌标记。
