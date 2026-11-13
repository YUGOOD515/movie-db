# 前端设计

页面在服务端由 Jinja 渲染，前端用 HTML、CSS、原生 JavaScript 和 Bootstrap 5.3。本文只记录读代码不容易看出的设计决定和原因；交互细节（底部状态栏、弹窗、动效）见[开发 · 前端约定](../zh-CN/development.md#前端约定)。

## 组件选择

- 先找 Bootstrap 原生组件和工具类（按钮、表单、下拉、弹窗、offcanvas、toast、间距类），找不到再写自定义 CSS。自定义 CSS 只用于媒体展示（海报卡片、轨道、详情页头部）和 Bootstrap 没有的交互。原因：样式表已经很大，原生组件能自动适配深浅色和无障碍。
- 模板只负责显示。卡片、徽章、进度、文案在 `viewmodels/` 里准备好，模板里不做计算和拼接，这样只读快照和本机页面能共用同一套模板。
- 颜色写成 `style.css` 里的变量，并在 `[data-bs-theme="light"]` 下给出浅色值；只写深色值会让浅色主题出问题。
- 强调色是中性单色 `--media-accent`：深色主题白色，浅色主题近黑（白色在浅底上看不见），勾和圆点用 `--media-accent-contrast`。Bootstrap 的聚焦、选中、激活色在编译时写死为蓝色，不随变量变化，所以统一在 `style.css` 顶部的 Accent 覆盖层改成强调色。这是对“优先用原生组件”的补充：组件照用，新用到的组件露出蓝色时补进覆盖层，不在页面里局部覆盖，也不为此另写组件。

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

## 圆角与曲率（G2）

G2 指轮廓连接处切线方向和曲率连续。全站常规圆角矩形以此为设计目标，统一使用 `border-radius` 配合超椭圆形状 [`corner-shape: squircle`](https://www.w3.org/TR/css-borders-4/#propdef-corner-shape)；只设置半径不能表达这项形状约定。

- 适用范围：卡片、海报与缩略图外框、普通按钮、输入框、选择器、菜单、弹窗、抽屉和状态栏等站内圆角矩形容器。管理页与媒体展示页使用同一规则，深浅色主题一致。
- 半径按元素用途复用 `--radius-card`、`--radius-control` 等现有令牌；形状统一不要求所有元素使用同一半径。
- 子元素贴合父级圆角时，形状一并设置 `corner-shape: inherit`，半径扣除父级边框宽度，避免悬停底色或图片在四角露缝。
- 几何例外：状态点、进度环、播放按钮、轨道翻页按钮等明确要求正圆的元素，以及明确要求半圆端点的胶囊形组件，保留圆形或圆弧。覆盖全站控件默认形状时显式设置 `corner-shape: round`；仅使用 `--radius-pill` 不代表自动豁免。
- 系统原生弹出界面、图标、Logo 和图片内部图形不在这项容器轮廓规范内。
- 兼容性例外：不支持 `corner-shape` 的浏览器保留 `border-radius` 普通圆角回退，不要求回退轮廓满足 G2。

## 管理页 Design system

设置页、网盘页和任务详情抽屉共用 `style.css` 中“Design system”一段的 `ui-*` 类。新的管理类页面直接复用这些类，不另写一套。

| 对齐线 | 位置 | 落在这条线上的元素 |
|---|---|---|
| A | 页面内容左缘 | 页头标题、分组卡片外缘 |
| B | A + `--ui-inset`（20px） | 分组图标块、行标签、分隔线、按钮行文字 |
| C | 12 栏中第 6 栏起点 | 所有输入控件的左缘 |
| D | A 的镜像 | 检测按钮、开关、输入框右缘 |

- `ui-row` 是 12 栏网格：标签占 5 栏、控件占 7 栏，所以每张卡片的控件都从 C 线开始。
- 层级：页头（h3）→ 分组标题（1rem / 600）→ 行标签（0.9375rem / 500）→ 说明（0.8125rem / 次要色）。
- 设置页的状态标签紧跟分组标题，右侧只放操作。正常时统一写“正常”（`services/settings.py` 的 `OK`），补充信息放在“·”后面；未配置 / 已关闭为灰色，异常写具体原因，不另造“已就绪”“已开启”之类的词。
- 不常改的配置（网盘速率、扫描、代理等）只放 `.env`，不进设置页；`SECTIONS` 只登记页面上要显示的项。
- 分组之间用 `--ui-group-gap`（40px）留白分开；组内行之间用缩进细线；卡片靠底色区分，不加描边。
- 分区色（`data-hue`）只给图标块用，不表达状态；分区对应的颜色登记在 `services/settings.py` 的 `SECTIONS`。

## 脚本

- `site.js` 只放全站行为，并通过 `window.*` 暴露公共函数（`appIcon`、`showAppToast` 等）；页面脚本按页拆分（`detail.js`、`manager.js` 等），需要公共能力时复用这些函数。
- 下拉选择给 `<select>` 加 `data-ui-select`：`site.js` 的 `enhanceSelect` 换成 Bootstrap 下拉（`.media-menu`），因为原生弹出列表由系统绘制、和站内菜单不一致。原生 select 保留取值并触发 `change`；脚本直接改 `value` 或选项后调用 `window.syncSelect(select)`。
- 动效集中在 `effects.js`，并在系统开启“减少动态效果”时关闭。
- 文件名识别规则 `filename-rules.js` 有独立测试：`node --test tests/test_filename_rules.cjs`。

## 第三方库与图标

- 第三方库放在 `static/vendor/` 并在其 README 登记版本，不走 CDN：云端只读快照和离线使用都要能加载。
- 通用图标来自 Lucide sprite。新图标要先加入 `scripts/build_icons.py` 的 `ICONS` 清单，再运行 `uv run python scripts/build_icons.py` 重新生成；`tests/test_icons.py` 会检查模板引用的图标是否都在 sprite 里。
- 评分平台 Logo 放在 `static/icons/ratings/` 并提交到仓库，因为安装包只打包 Git 跟踪的文件。
