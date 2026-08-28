# 详情页季与版本交互调整

## 目标

合并季选择与季摘要；将每季单集改为横向滚动轨道；让 Hero 和单集中央播放按钮自身承担多版本选择；统一按钮 G2 圆角。

## 阶段

- [complete] 核对当前详情模板、CSS、版本数据结构与测试
- [complete] 重构播放按钮及季选择模板
- [complete] 将单集网格改为横向轨道并补充必要 CSS
- [complete] 更新测试并验证生产数据页面结构

## 结果

- 季 Tab 直接显示季名、年份、集数、评分和简介摘要，Pane 内不再重复季摘要。
- 每季单集使用无滚动条的 Bootstrap Flex 横向轨道；桌面每张卡占 3 单位，末尾保留留白。
- Hero 和单集中央播放键：单版本直接播放，多版本由原按钮展开 Dropdown；独立版本按钮与底部版本列表均已删除。
- 全部按钮使用 G2 `corner-shape: squircle`，保留 Bootstrap 默认圆角大小。
- 12 项测试通过；生产库多版本电影和单集 Dropdown、季 Pane 结构均通过只读验证；diff 空白检查通过。

## 解释与边界

- “选季中合并显示季数据”实现为：每个季 Tab 直接显示季名、年份、集数、评分和简介摘要，移除 Tab Pane 内重复的季摘要块。
- 单版本播放按钮保持直接播放；仅多版本时按钮变为 Bootstrap Dropdown。
- 不新增自定义 JavaScript，使用 Bootstrap Dropdown 与 Tab。
- 所有 `.btn` 使用 `corner-shape: squircle`，保持 Bootstrap 默认圆角尺寸；移除强制 `rounded-circle`。
- 不使用 design-taste-frontend skill，不进行视觉检查。

## 错误记录

- 一次组合补丁因 hunk 边界格式错误未应用；拆分补丁后成功应用，未影响代码内容。
- 首轮回归有 1 项失败：旧测试仍查找独立的“播放版本 · 2”文案；实现已按要求将版本入口合并进播放按钮，测试同步改为验证 Hero 和单集两个 Dropdown 按钮。
