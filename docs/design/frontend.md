# 前端设计

页面在服务端由 Jinja 渲染，前端用 HTML、CSS、原生 JavaScript 和 Bootstrap 5.3。本文只记录读代码不容易看出的设计决定和原因；交互细节（底部状态栏、弹窗、动效）见[开发 · 前端约定](../zh-CN/development.md#前端约定)。

## 组件选择

- 先找 Bootstrap 原生组件和工具类（按钮、表单、下拉、弹窗、offcanvas、toast、间距类），找不到再写自定义 CSS。自定义 CSS 只用于媒体展示（海报卡片、轨道、详情页头部）和 Bootstrap 没有的交互。原因：样式表已经很大，原生组件能自动适配深浅色和无障碍。
- 模板只负责显示。卡片、徽章、进度、文案在 `viewmodels/` 里准备好，模板里不做计算和拼接，这样只读快照和本机页面能共用同一套模板。
- 颜色写成 `style.css` 里的变量，并在 `[data-bs-theme="light"]` 下给出浅色值；只写深色值会让浅色主题出问题。

## 网格与间距

目标：同一页上的卡片、标题和控件落在几条固定的竖线上。

### 主对齐线

页面内容包在 `container-xl` 里，容器的左右内边缘就是主对齐线：页头标题、区块标题、卡片网格、轨道里的第一张卡片都从这里开始。只有横向轨道和详情页背景图会越过容器边缘（出血）。

### 卡片水平间距

全站只有一套卡片间距：

| 视口宽度 | 间距 | 卡片网格 | 横向轨道 |
|---|---|---|---|
| < 992px | 1rem | `g-3` | `gap-3` |
| ≥ 992px | 1.5rem | `g-lg-4` | `gap-lg-4` |

- 同一页上下相邻的网格和轨道用同一档间距，卡片的左右边缘才能连成竖线。
- `.media-rail` 的 `--rail-gap` 只用来算轨道末尾的留白，真正的间距来自模板里的 `gap-*`。两处数值要一起改，只改一处会让轨道末尾的留白和卡片间距对不上。
- 详情页内部统一 1rem（`gap-3`），与 `--grid-gap` 一致。

### 12 栏精确定宽

需要按栏定宽的区域（详情页头部、剧集轨道、演员轨道）用容器查询计算：

```css
--grid-gap: 1rem;
--grid-col: calc((100cqi - 11 * var(--grid-gap)) / 12);
```

- 先给容器加 `container-type: inline-size`，并登记在 `style.css` 的“Grid containers”选择器里；没有容器时 `100cqi` 会退回视口宽度，算出来的宽度就错了。
- 跨 N 栏 = `N × --grid-col + (N − 1) × --grid-gap`，例如 3 栏写成 `calc(var(--grid-col) * 3 + var(--grid-gap) * 2)`。不要用百分比或固定 px 估算宽度。
- 与栏宽相关的高度也从栏宽派生（如 `--btn-unit-height: calc(var(--grid-col) / 2)`），让比例随宽度一起变化。

### 垂直间距与令牌

- 垂直间距用 Bootstrap 间距类（`mb-*`、`gy-*`）；区块之间用留白分隔，不加分隔线或边框。
- 新尺寸先在 `style.css` 顶部加令牌再使用（圆角 `--radius-*`、阴影、动效曲线等），不要在规则里散写数值。
- 圆角统一使用超椭圆（`corner-shape: squircle`）。子元素继承父级圆角时要写 `corner-shape: inherit`，并把半径减去父级边框宽度。

## 管理页 Design system

设置页、网盘页和任务详情抽屉共用 `style.css` 中“Design system”一段的 `ui-*` 类。新的管理类页面直接复用这些类，不另写一套。

| 对齐线 | 位置 | 落在这条线上的元素 |
|---|---|---|
| A | 页面内容左缘 | 页头标题、分组卡片外缘 |
| B | A + `--ui-inset`（20px） | 分组图标块、行标签、分隔线、按钮行文字 |
| C | 12 栏中第 6 栏起点 | 所有输入控件的左缘 |
| D | A 的镜像 | 状态标签、检测按钮、开关、输入框右缘 |

- `ui-row` 是 12 栏网格：标签占 5 栏、控件占 7 栏，所以每张卡片的控件都从 C 线开始。
- 层级：页头（h3）→ 分组标题（1rem / 600）→ 行标签（0.9375rem / 500）→ 说明（0.8125rem / 次要色）。
- 分组之间用 `--ui-group-gap`（40px）留白分开；组内行之间用缩进细线；卡片靠底色区分，不加描边。
- 分区色（`data-hue`）只给图标块用，不表达状态；分区对应的颜色登记在 `services/settings.py` 的 `SECTIONS`。

## 脚本

- `site.js` 只放全站行为，并通过 `window.*` 暴露公共函数（`appIcon`、`showAppToast` 等）；页面脚本按页拆分（`detail.js`、`manager.js` 等），需要公共能力时复用这些函数。
- 动效集中在 `effects.js`，并在系统开启“减少动态效果”时关闭。
- 文件名识别规则 `filename-rules.js` 有独立测试：`node --test tests/test_filename_rules.cjs`。

## 第三方库与图标

- 第三方库放在 `static/vendor/` 并在其 README 登记版本，不走 CDN：云端只读快照和离线使用都要能加载。
- 通用图标来自 Lucide sprite。新图标要先加入 `scripts/build_icons.py` 的 `ICONS` 清单，再运行 `uv run python scripts/build_icons.py` 重新生成；`tests/test_icons.py` 会检查模板引用的图标是否都在 sprite 里。
- 评分平台 Logo 放在 `static/icons/ratings/` 并提交到仓库，因为安装包只打包 Git 跟踪的文件。
