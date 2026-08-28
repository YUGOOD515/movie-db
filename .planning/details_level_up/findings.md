# Findings

## 来自 ref.md 的实施结论

- 首屏新增：评分与评分人数、Genres、原名、Tagline、TV 季数/集数/状态。
- 首屏以下新增：制作国家、语言、制作公司、Creator/Network、Collection、外部链接。
- 剧集区优先实现季选择、单集列表、多版本选择。
- 年龄分级当前数据库链路不完整，应继续显示缺省状态，不能伪造。
- 媒体技术信息尚未结构化，不根据文件名声称 4K/HDR/DV 等确定属性；版本入口可直接使用文件名。

## 项目约束

- Model 查询集中在 models.py，app.py 只负责编排，Jinja 负责展示。
- 当前项目前端以 Bootstrap 为主，少量自定义 CSS；明确禁用 design-taste-frontend skill。

## 现有实现核对

- 电影 `get_media_detail()` 已返回全部 `movie_source`，但没有版本标签与选择界面。
- TV 查询使用 `ROW_NUMBER() ... source_rank = 1`，同一单集只保留一个 source；需要改为查询所有 source 后按季、单集聚合。
- `tv_season` 已有季名、日期、集数、评分、简介；页面可展示这些字段，但按既定要求不使用季竖版海报。
- 国家、语言、制作公司均为规范化关系表；Creator 与 Network 保存在 TV JSON 字段中。
- 当前 Hero 是绝对定位内容，新增信息后存在高度溢出风险；应改为由 Flex 底部对齐且让内容参与 Hero 高度计算。

## 验证结论

- 生产库详情结构聚合结果与数据库一致：电影 source 13/13、TV source 715/715、单集 490/490。
- 222 个拥有多个 source 的单集均在新结构中保留，不再受旧 `source_rank = 1` 过滤。
- 9 个电影详情路由和 22 个剧集详情路由均只读渲染成功，状态码全部为 200。
