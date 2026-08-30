当前已经保存了相当多的数据，但详情页目前只用了其中很小一部分。
### 现有但未展示的数据：按加入优先级

| 优先级 | 数据 / 功能                                                      | 当前情况                                                                          | 建议怎么加入                                      | 原因                              |
| --- | ------------------------------------------------------------ | ----------------------------------------------------------------------------- | ------------------------------------------- | ------------------------------- |
| 1   | **类型 Genres**                                                | 电影 9/9、剧集 22/22 都有；甚至已经传入 `media.genres`，模板却没用                                | Hero 信息行直接显示：`科幻 · 剧情 · 悬疑`                 | 信息价值高、零额外数据工作，几乎应该立即加           |
| 2   | **TMDB 评分 + 评分人数**                                           | `vote_average`、`vote_count` 已经传到详情页                                           | `★ 8.7 · 12,450 人评分`                        | 首页已经显示评分，详情页反而没有，不一致            |
| 3   | **资源版本选择**                                                   | 9 部电影中 2 部有 3 个版本；477 个有资源的单集中，**222 集有多个版本**，最多 3 个；当前 TV 查询只保留最新一个          | 播放按钮旁增加“版本”菜单，例如 `4K HDR / 1080P`，初期可先使用文件名 | 这是目前真正被“隐藏掉的可播放内容”，优先级甚至高于很多元数据 |
| 4   | **剧集按季组织**                                                   | 已有 51 个 `tv_season`，但详情页把所有有资源的单集平铺到一个列表                                      | 增加 `第1季 / 第2季 / 第3季` Tab 或下拉选择              | 现在剧集一多，单集列表会迅速失控                |
| 5   | **剧集总季数 / 总集数 / 状态**                                         | `number_of_seasons`、`number_of_episodes`、`series_status`、`in_production` 都已保存 | 标题下面：`2022–至今 · 3季 · 30集 · 连载中`             | 对电视剧属于基本信息                      |
| 6   | **Tagline**                                                  | 电影 6/9、剧集 6/22 有                                                              | Logo/标题下或简介上方用一句较淡的文字                       | 有内容时视觉效果很好，但不能依赖它，因为覆盖率不高       |
| 7   | **原始片名**                                                     | 电影、剧集全部有                                                                      | 中文标题下显示较小的 `Original Title`                 | 对你的中文媒体库很有用，同时成本极低              |
| 8   | **电影观看进度条**                                                  | 数据已有；剧集卡片显示进度，但电影详情页没有可视进度                                                    | 播放按钮下方或背景底部加细进度条                            | “继续播放”只有文字，没有视觉反馈               |
| 9   | **季信息**                                                      | 51 季都有名称、集数、评分；49 季有日期和海报；27 季有简介                                             | 切换季时显示季海报、年份、集数和简介                          | 配合第 4 项一起做价值很高                  |
| 10  | **主创 / 电视网**                                                 | 20/22 部剧有 creator；22/22 有 network                                             | `创作：...`、`HBO / Netflix` 等放简介下方             | 有辨识度，不宜抢 Hero 主信息               |
| 11  | **制作公司 / 国家 / 语言**                                           | 电影和绝大多数剧集数据都完整                                                                | 做成“影片信息”区域：`美国 · 英语 · Warner Bros.`         | 适合作为第二信息层                       |
| 12  | **电影所属系列 Collection**                                        | 当前 9 部电影中 2 部属于 Collection                                                    | 显示“属于 ×× 系列”，以后可点进去查看系列                     | 数据少但未来媒体库扩大后价值上升                |
| 13  | **外部链接**                                                     | 电影 IMDb ID 全部有；电影/剧集 TMDB ID 本身就是主键；部分有 homepage                              | 详情底部放 `TMDB / IMDb / 官网` 图标链接               | 非核心，但实现极简单                      |
| 14  | **多张图片**                                                     | 已存 **130 张电影图片、269 张剧集图片**，现在只取第一张 backdrop/logo，poster 在详情页完全不用              | 页面下方可选“剧照/艺术图”横向画廊                          | 有现成资产，但不应抢主要信息                  |
| 15  | **单集评分**                                                     | 490 集全部保存评分和 vote count                                                       | 单集卡右侧小字 `★ 8.4`                             | 可加，但单集卡已有时长/日期/进度，注意避免过密        |
| 16  | **单集简介**                                                     | 448/490 有；现在只有“当前 featured episode”展示简介                                       | 点击/展开单集时显示，而非每张卡直接铺开                        | 数据价值高，但直接全部展示会很拥挤               |
| 17  | **单集 Crew / Guest Stars**                                    | 402 集有 crew、423 集有 guest stars                                                | 单集展开页或弹层再显示                                 | 数据很丰富，但不适合主剧集页面                 |
| 18  | **预算 / 票房 / Popularity**                                     | 电影全部保存                                                                        | 放“更多信息”折叠区域                                 | 更像资料库数据，对个人观看媒体库价值较低            |
| 19  | **AList 原始路径**                                               | 每个 source 都有                                                                  | 只在高级/管理菜单显示                                 | 对播放有用，但普通详情页没有视觉价值              |
| 20  | `adult / softcore / video / production_code / internal id` 等 | 已保存                                                                           | 默认不展示                                       | 数据库维护数据，不属于正常用户界面               |

这里第 **3 项尤其值得处理**。你目前有 715 条单集 source，但只有 477 个单集拥有资源，其中 206 集有 2 个版本、16 集有 3 个版本。当前 SQL 的 `ROW_NUMBER() ... source_rank = 1` 会让这些备用版本在详情页完全不可见。

另外还有三类重要内容**目前不是“已有但没展示”，而是数据库本身尚未完整拥有**：

* 年龄分级：页面现在显示“年龄分级 未提供”，实际上 `get_media_detail()` 直接把 `age_rating = None`。
* 电影/剧集演员、导演、编剧：目前没有电影 credits 数据；TV 只有 creator，以及单集 crew / guest stars。
* **媒体文件技术信息**：分辨率、HEVC/AV1、HDR10/Dolby Vision、码率、音轨、Atmos、字幕等均未结构化存储。考虑到你本身有 4K/HDR/DV 资源，这一项以后很值得通过 `ffprobe` 补上。

---

## 三套搭配方案

### 方案 A：Infuse 式——沉浸、极简

我会把数据控制在：

> Logo/标题 → `年份 · 时长 · ★评分 · 类型` → Tagline → 简介 → 播放/继续播放
> 剧集再增加：`季选择 → 单集卡`

次要的制作公司、国家、语言、TMDB 链接全部放下面，默认不抢注意力。

这和你现在的设计方向最接近：大 Backdrop、Logo、文字相对少。Infuse 本身也主要强调 artwork、核心年份/评分/格式、简介和播放操作，视觉优先。Firecore 的 Metadata 系统也是以 TMDB 的标题、简介、演员、海报和 fanart 为核心。([Firecore支持][1])

[Infuse Metadata 101](https://support.firecore.com/hc/en-us/articles/215090947-Metadata-101?utm_source=chatgpt.com)

优点是最好看、最不容易做成“数据库管理器”；缺点是你已经保存的大量 metadata 利用率仍然不高。

---

### 方案 B：Plex 式——视觉和信息平衡，我最推荐

Hero 保持你现在的大背景和 Logo，但信息变成：

> `2024 · 128 分钟 · PG-13 · ★ 8.6 · 科幻 / 剧情`
> Tagline
> 简介

下面增加一个中密度信息区：

> 原名　制作国家　语言　制作公司
> IMDb / TMDB
> 播放版本：`4K DV` / `1080P`

TV：

> `2022–至今 · 共 3 季 24 集 · 连载中`
> 创作者 · Network
> `[第1季] [第2季] [第3季]`
> 单集卡

Plex 官方的详情页本身就是这种思路：海报和影片视觉仍然重要，但同时显示年份、时长、类型、评分、年龄分级、导演、演员、音轨/字幕、简介和分辨率等。([Plex Support][2])

[Plex 官方：Viewing Item Details](https://support.plex.tv/articles/202462186-viewing-item-details/?utm_source=chatgpt.com)

这最适合你的项目，因为你既不是单纯做漂亮的影片展示页，也不是做 Jellyfin 那种服务器后台；你确实需要查看**版本、观看状态和媒体资料**。

---

### 方案 C：Jellyfin 式——资料库 / Power User

Hero：

> Poster + Backdrop + Logo
> `年份 · 时长 · 状态 · ★评分 · 类型`

主体直接展示：

> 原名
> Tagline
> 简介
> 制作公司 / 国家 / 语言
> Creator / Network
> TMDB / IMDb
> Collection

资源：

> `版本 1 — 4K Dolby Vision`
> `版本 2 — 1080P SDR`
> AList 路径（展开后）
> 视频 / 音频 / 字幕参数

剧集：

> 季海报 + 季评分 + 季简介
> 单集剧照 + 评分 + 时长 + 日期 + 简介
> 展开后 Guest Stars / Crew

Jellyfin明显偏这种信息密度，官方甚至把 Details Screen 定义为展示媒体全部相关信息的主要界面；它也原生重视多个版本、外部字幕、artwork 和文件级媒体信息。([Jellyfin][3])

[Jellyfin 官方网站与详情页示例](https://jellyfin.org/?utm_source=chatgpt.com)
[Jellyfin Movies / Multiple Versions 文档](https://jellyfin.org/docs/general/server/media/movies/?utm_source=chatgpt.com)

优点是数据利用率最高；缺点是很容易让你的详情页从“看片界面”变成“媒体服务器界面”。

### 三种实际案例视觉参考

下面依次可以重点观察 **Infuse 的信息克制、Plex 的中等密度、Jellyfin 的资料化布局**：

![Image](https://static.firecore.com/images/blog/infuse75-trailers_2x.jpg)

![Image](https://images.openai.com/static-rsc-4/_veinSOjVC8YliDrj-q8Tpv5EjhJS8D1wEfsD1XBJbPXW_1EoaQPfDu-B-E8_PPQaqnJ7BtvaTex4rIYwtytoQ4LaNIUbqKnHgNRgjk2wxJOmo4r_Kpvd6rT3MxhpB0zXvDac7E0T2ZL035YvqtFPKxHBVtVkt3t8raYH6zOjfXz0NshXS7sqzhz0NNKAN_-?purpose=fullsize)

![Image](https://jellyfin.org/assets/images/10.8-details-df4f7caaee389ffbaeeaa95b668fa037.png)

---

如果按照你这个项目目前的定位，我建议采用 **方案 B 的数据量 + 方案 A 的视觉风格**，而不是完整复制 Plex。

也就是首屏只增加：

**`评分 + Genres + 原名 + Tagline + TV季数/状态`**

然后优先重构剧集部分：

**`季选择 + 单集列表 + 多版本选择`**

制作公司、语言、国家、外部 ID、季简介等放到首屏下面。这样既明显提高信息量，又不会破坏你现在已经做好的大背景 + Logo 的视觉重心。

[1]: https://support.firecore.com/hc/en-us/articles/27862264977047-Library-Scanning-Indexing?utm_source=chatgpt.com "Library Scanning & Indexing – Firecore Support"
[2]: https://support.plex.tv/articles/202462186-viewing-item-details/?utm_source=chatgpt.com "Viewing Item Details | Plex Support"
[3]: https://jellyfin.org/docs/general/server/media/movies/?utm_source=chatgpt.com "Movies | Jellyfin"
