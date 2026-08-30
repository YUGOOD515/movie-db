# Findings

- 项目约定要求详情页采用局部 Flask/Jinja/CSS 修改，不做数据层重构。
- 类别筛选页 `genre_page.html` 已存在年份、评分、制作国家下拉筛选，可作为 details 页组件基准。
- 仓库仅有 `detail.html`，其中没有年份、评分、制作国家选择控件；这些筛选目前只出现在 `genre_page.html`。需要继续定位用户所称的 “details/选择媒体元素和类别” 是否对应 HTML 原生 `details` 区块或媒体管理页。
- 最近观看播放按钮使用 `play-button btn btn-dark`；类别筛选使用 `genre-dropdown-toggle` 与 `genre-pill-btn`，媒体管理页另有 segmented control，三者存在可复用基础。
- 全仓库没有独立的 `details` 模板或 `<details>` 区块；实际详情模板为 `detail.html`。类别页没有独立播放按钮，因此“类别中同款”更合理地解释为沿用类别页当前白色/中性色选择控件风格。
- 蓝色来源主要是 Bootstrap `btn-primary`、`text-primary`、`bg-primary` 以及类别激活态引用的 `--bs-primary`；应在项目 CSS 统一成白色强调态，同时为浅色主题提供反色，避免白底白字。
- 类别页的年份、评分、制作国家按钮已经共享 `genre-dropdown-toggle`，尺寸为 2rem 高；“全部/电影/剧集”和类别药丸也已有固定尺寸。本轮应保留这些尺寸，仅调整强调色。
- 可新增统一的 `media-primary-action`：详情播放、最近观看圆形播放、媒体管理提交/保存按钮共同使用；深色主题为白底深字，浅色主题反转为深底白字。
- 最近观看与单集卡片的进度条依赖 Bootstrap 默认蓝色，详情选中徽标也使用 `text-bg-primary`；若只改按钮仍会残留蓝色，需要一并接入单色强调变量。
- “查看全部”及媒体类型/类别选择按钮均使用 secondary/segmented/pill 样式，不应改动其高度、padding 或字号。
- 详情当前集标识文本已是中性色，不需额外改色；需要处理的详情蓝色只剩播放按钮、当前徽标、选中描边与进度条。
- 现有测试仅检查类别页文字，尚未约束三个主要下拉框的共同类或“全部”按钮尺寸类，可增加结构断言防止后续漂移。
- `.episode-selected-badge` 位于样式文件约 745 行，当前仅控制定位/显示；可在该规则内直接加入单色背景与对比文字。
- 修改后模板和项目 CSS 中已无 `btn-primary`、`text-primary`、`text-bg-primary` 或 `--bs-primary` 引用；统一强调色覆盖主操作、进度、选中描边、导航与类别激活态。
