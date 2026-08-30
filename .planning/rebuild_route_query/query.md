三层读取结构：

```text
route
  ↓
query          数据库查询，只负责“取什么”
  ↓
viewmodel      整理数据，只负责“前端需要什么形状”
  ↓
Jinja          显示，只负责 HTML
```

不要上完整 MVC、ORM、Repository/Service 一大套。对个人 Flask 项目来说会增加代码量。Flask 本身也不强制项目架构；官方教程建议随着项目扩大再做模块化。([Flask 文档][1])

## 一、代码划分

目录保持比较轻：

```text
movie-project/
│
├── app.py
├── db.py
│
├── queries/
│   ├── home.py
│   ├── catalog.py
│   ├── title.py
│   ├── collection.py
│   └── manager.py
│
├── viewmodels/
│   ├── home.py
│   ├── cards.py
│   ├── title.py
│   └── manager.py
│
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── catalog.html
│   ├── collection.html
│   ├── title.html
│   ├── manager.html
│   └── components/
│       ├── poster_card.html
│       ├── backdrop_card.html
│       ├── progress.html
│       └── ...
│
└── static/
    ├── css/
    └── js/
```

三层职责必须尽量严格。

`queries/` 只允许接收条件、执行 SQL、返回数据库数据。例如：

```python
get_recently_watched(db)
get_recently_added(db, limit=12)
get_unwatched(db)
get_top_rated_movies(db, limit=9)
get_title_detail(db, title_id)
```

这里不要生成 `"2024-2026"`、不要计算 `"S02E05"`、不要决定用 series poster 还是 episode poster。

`viewmodels/` 专门负责这些事情：

```python
format_year(title)
build_poster_card(row)
build_recent_watch_card(row)
build_title_page(rows)
build_genre_sections(rows)
```

最后得到前端直接使用的数据：

```python
{
    "title": "绝命毒师",
    "subtitle": "S02E05 · 破坏",
    "poster": "...",
    "backdrop": "...",
    "year": "2008-2013",
    "progress": 0.63
}
```

Jinja 不应该再知道：

```python
if episode:
    找 parent series
    获取 series poster
    position / duration
    start_year + "-" + end_year
```

它只负责：

```jinja2
<h3>{{ item.title }}</h3>
<p>{{ item.subtitle }}</p>

{% if item.progress %}
    ...
{% endif %}
```

Jinja 官方本身也建议应用逻辑尽量留在 Python；模板继承、macro、include 则适合解决重复 HTML。([Jinja][2])

---

## 二、`app.py` 应该长什么样

你想要的“像函数调用链条一样”非常适合这样写：

```python
@app.get("/")
def index():
    db = get_db()

    raw = query_home(db)
    page = build_home_viewmodel(raw)

    return render_template("index.html", page=page)
```

详情页：

```python
@app.get("/title/<title_id>")
def title(title_id):
    db = get_db()

    raw = query_title(db, title_id, request.args)
    page = build_title_viewmodel(raw)

    return render_template("title.html", page=page)
```

收藏夹：

```python
@app.get("/collection/<int:collection_id>")
def collection(collection_id):
    db = get_db()

    raw = query_collection(db, collection_id)
    page = build_collection_viewmodel(raw)

    return render_template("collection.html", page=page)
```

这就是我认为最适合你的 route 复杂度：

```text
获取请求参数（如果需要）
↓
数据库查询
↓
整理
↓
render_template
```

route 最好控制在大约 5～15 行。

---

# 三、首页如何拆

首页虽然区域很多，但不要让 `index()` 出现二三十个函数调用。

建议：

```python
def query_home(db):
    return {
        "recent_watch": query_recent_watch(db),
        "collections": query_collections(db),
        "recent_added": query_recent_added(db),
        "movie_genres": query_genres(db, "movie"),
        "series_genres": query_genres(db, "series"),
        "unwatched": query_unwatched(db),
        "top_movies": query_top_movies(db),
    }
```

然后：

```python
def build_home_viewmodel(raw):
    return {
        "recent_watch": build_recent_watch(raw["recent_watch"]),
        "collections": build_collection_cards(raw["collections"]),
        "recent_added": build_poster_cards(raw["recent_added"]),
        "movie_genres": build_genre_cards(raw["movie_genres"]),
        "series_genres": build_genre_cards(raw["series_genres"]),
        "unwatched": build_poster_cards(raw["unwatched"]),
        "top_movies": build_poster_cards(raw["top_movies"], rating=True),
    }
```

route 仍然只有：

```python
raw = query_home(db)
page = build_home_viewmodel(raw)
return render_template(...)
```

这是“页面级查询 + 页面级整理”。

---

## 四、几个最重要的通用 ViewModel

你现在很多页面其实只反复出现 3～4 种 UI 数据结构。与其每个页面重新整理数据，不如固定它们。

### `PosterCard`

最近添加、未观看、类别、收藏、排行榜第 4～9 名都可以使用同一个结构：

```python
{
    "title_id": "tt0903747",
    "title": "绝命毒师",
    "type": "series",
    "year": "2008-2013",
    "poster": "...",
    "rating": 9.5
}
```

不需要评分：

```python
rating = None
```

Jinja component 自己决定是否显示。

这样：

```text
最近添加
未观看
类别筛选
收藏夹
Top 4-9
```

全部复用同一个 `poster_card.html`。

### `BackdropCard`

你的首页“最近观看”和排行榜前三的简易背景板可以基于一个统一结构：

```python
{
    "title_id": "...",
    "logo": "...",
    "backdrop": "...",
    "title": "...",
    "subtitle": "...",
    "rating": 9.3,
    "progress": 0.54
}
```

页面需要哪个字段就显示哪个。

### `MediaBadge`

详情页的：

```text
4K
HEVC
Dolby Vision
HDR10
7.1
```

也不要让 Jinja 根据 `video_width / codec / dynamic_range` 自己判断。

Python：

```python
badges = build_media_badges(media_file)
```

得到：

```python
["4K", "HEVC", "Dolby Vision", "7.1"]
```

模板：

```jinja2
{% for badge in page.badges %}
    <span>{{ badge }}</span>
{% endfor %}
```

前端会干净很多。

---

# 五、最近观看是最值得认真设计的查询

这是你整个项目里查询关系比较复杂的一块。

最终 SQL 应直接取出：

```text
playback_progress
→ media_file
→ title_basic

如果 episode：
→ title_relation
→ series title_basic
→ series artwork
```

查询阶段就应该拿到类似：

```text
watched title
watched_at
position_ms
duration_ms

episode title
season_number
episode_number

series title
series artwork
```

然后 ViewModel 判断类型。

movie：

```text
星际穿越
2014
backdrop
62%
```

episode：

```text
绝命毒师
S02E05 · 破坏
series backdrop
62%
```

也就是说：

> SQL 负责 JOIN，Python 负责解释 JOIN 后的数据。

不要 Python 先查询最近观看 10 条，然后循环 10 次分别查 series、poster、duration。这属于典型 N+1 查询，代码和性能都会变差。

---

# 六、genres 建议放在 Python 整理

你现在每个 `title_basic.genres` 最多三个：

```text
Drama,Crime,Thriller
```

对于你的个人影视库规模，没必要为了首页分类专门做复杂 SQL。

查询：

```sql
SELECT
    title_id,
    genres,
    ...
FROM title_basic
WHERE title_type = 'movie';
```

Python：

```python
for title in rows:
    for genre in title["genres"].split(","):
        ...
```

最终：

```python
[
    {
        "name": "Drama",
        "count": 43,
        "backdrop": "..."
    },
    {
        "name": "Sci-Fi",
        "count": 18,
        "backdrop": "..."
    }
]
```

这正属于“整理数据”，不属于数据库查询。

不过类别筛选页的：

```text
genre = Sci-Fi
```

仍然应该在 SQL 中筛选，而不是把整个数据库读到 Python 后再筛。

---

# 七、类别筛选页

route：

```python
@app.get("/catalog")
def catalog():
    filters = parse_catalog_filters(request.args)

    raw = query_catalog(get_db(), filters)
    page = build_catalog_viewmodel(raw, filters)

    return render_template("catalog.html", page=page)
```

这里 `filters` 可以统一为：

```python
{
    "type": "movie",
    "genre": "Sci-Fi",
    "year": 2020,
    "rating": 8.0,
    "language": "en",
    "sort": "rating"
}
```

然后 `query_catalog()` 动态构造：

```sql
WHERE ...
ORDER BY ...
```

而不是：

```text
/movie/genre/
/series/genre/
/movie/year/
/series/year/
...
```

所以你只需要一个：

```text
/catalog
```

配合 query string：

```text
/catalog?type=movie&genre=Sci-Fi&rating=8&sort=rating
```

这样会少很多 route。

---

# 八、详情页建议只保留一个 route

不要：

```text
/movie/<id>
/series/<id>
/episode/<id>
```

而是继续统一：

```text
/title/<title_id>
```

电影：

```text
/title/tt0816692
```

series：

```text
/title/tt0903747
```

选季、选集：

```text
/title/tt0903747?season=2&episode=5
```

查询层：

```python
query_title(...)
```

内部：

```text
获取 title_basic
↓
判断 movie / series / episode
↓
获取 artwork
↓
获取 media_file
↓
获取 latest playback
↓
series 则额外获取 episodes
```

ViewModel 最终统一生成：

```python
page.hero
page.episodes
page.metadata
```

模板甚至不必分别建立 `movie_detail.html` 和 `series_detail.html`，一个 `title.html` 足够。

---

## Series 这里有一个逻辑需要分开

你当前描述中有两个概念：

```text
当前展示的 episode
继续播放的 episode
```

例如：

```text
/title/tt0903747
```

你要求默认展示 S1E1。

但如果用户之前已经看到 S3E7，那么“继续播放”需要打开 S3E7。

所以后端最好明确区分：

```python
page.selected_episode
page.resume_episode
```

默认：

```text
selected_episode = S1E1
resume_episode   = 最近观看 episode
```

如果用户点击 S2E4：

```text
selected_episode = S2E4
resume_episode   = 最近观看 episode
```

这样不会让 hero 展示 S1E1，却错误地把 S3E7 的 `position_ms` 当作 S1E1 的进度。

---

# 九、推荐的路由规模

按照你目前功能，我认为最终读取型 route 大概只需要：

| Route               | 页面/用途                           |
| ------------------- | ------------------------------- |
| `/`                 | 首页                              |
| `/recent`           | 最近添加                            |
| `/unwatched`        | 未观看                             |
| `/top`              | 最受好评                            |
| `/catalog`          | 类型、genre、年份、评分、语言筛选             |
| `/collection/<id>`  | 某个收藏夹                           |
| `/title/<title_id>` | movie / series / episode 详情统一入口 |
| `/manager`          | 媒体资源管理概览                        |
| `/play/<file_id>`   | 播放实际媒体文件                        |

加上之后的录入 route，也不会非常多。

`/play/<file_id>` 我倾向用：

```python
@app.post("/play/<int:file_id>")
```

而不是 GET，因为它会触发“打开 MPC-HC / 播放媒体”这种实际动作。

---

# 十、`db.py` 应该非常薄

Flask 官方推荐的 SQLite 模式本身就很适合你的项目：每个请求按需创建连接，存在 Flask `g` 中，请求结束后关闭，并设置 `sqlite3.Row` 让查询结果可以按字段名访问。([Flask 文档][3])

例如职责只有：

```text
get_db()
close_db()
```

我甚至不建议你现在封装成：

```python
DatabaseManager
Repository
DAO
```

这些对你的规模没有明显收益。

---

# 十一、Jinja 层怎么划

建议用：

```text
base.html
    ↓ extends

index.html
catalog.html
title.html
manager.html
```

然后公共 UI 使用 macro/component：

```text
components/poster_card.html
components/backdrop_card.html
components/progress.html
components/media_badges.html
```

Jinja 官方支持模板继承、`include` 和 macro，正适合这类重复卡片组件。([Flask 文档][4])

例如：

```jinja2
{% from "components/poster_card.html" import poster_card %}

{% for item in page.items %}
    {{ poster_card(item) }}
{% endfor %}
```

这样 Bootstrap HTML 也不会在五个页面复制五遍。

---

## 推荐学习资源

按你当前项目的实际重要程度学习即可：

| 优先级 | 官方资源                                                                                                                          | 你重点看什么                                                                      |
| --- | ----------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| 1   | [Flask：Define and Access the Database](https://flask.palletsprojects.com/en/stable/tutorial/database/?utm_source=chatgpt.com) | `get_db()`、`g`、SQLite 连接生命周期、`sqlite3.Row`，直接用于你的 `db.py`。                  |
| 2   | [Flask：Using SQLite 3 with Flask](https://flask.palletsprojects.com/en/stable/patterns/sqlite3/?utm_source=chatgpt.com)       | 更短、更接近实际项目的 SQLite + Flask 写法。                                              |
| 3   | [Flask：Templates](https://flask.palletsprojects.com/en/stable/tutorial/templates/?utm_source=chatgpt.com)                     | `render_template()`、模板继承、base layout。                                       |
| 4   | [Jinja：Template Designer Documentation](https://jinja.palletsprojects.com/en/stable/templates/?utm_source=chatgpt.com)        | macro、include、inheritance、filters；重点解决重复海报卡片。                               |
| 5   | [Python：sqlite3 官方文档](https://docs.python.org/3/library/sqlite3.html?utm_source=chatgpt.com)                                  | 参数化查询、Row、execute/fetch；不用全部读。                                              |
| 6   | [SQLite：WITH / CTE](https://www.sqlite.org/lang_with.html?utm_source=chatgpt.com)                                             | 以后写“最近观看”“最新一条 playback”等复杂 SQL 时非常有用；CTE 可以把长 SQL 拆成几个有名字的步骤。([SQLite][5]) |
| 7   | [SQLite：Window Functions](https://www.sqlite.org/windowfunctions.html?utm_source=chatgpt.com)                                 | 学 `ROW_NUMBER()`，非常适合“每个 title 最新播放记录”“每组排名”等查询。([SQLite][6])               |
| 8   | [Flask：Blueprints](https://flask.palletsprojects.com/en/stable/blueprints/?utm_source=chatgpt.com)                            | 暂时不用急着实现；等 `app.py` route 明显太多时再拆。                                          |
| 9   | [Bootstrap 5.3 Cards](https://getbootstrap.com/docs/5.3/components/card/?utm_source=chatgpt.com)                              | 海报卡片、背景卡片的 HTML 基础。                                                         |
| 10  | [Bootstrap 5.3 Grid](https://getbootstrap.com/docs/5.3/layout/grid/?utm_source=chatgpt.com)                                   | 最近添加、收藏、筛选结果的响应式排列。                                                         |

我会优先学到第 4 项就开始写项目；第 6、7 项遇到复杂查询再学，不需要先把 SQL 系统学完。

最终你的读取端可以统一成一句规则：

```text
app.py：描述流程
queries：把数据库中的正确数据一次查出来
viewmodels：把数据库数据变成页面数据
Jinja：只负责把页面数据画出来
```

对于你现在这个影视库，这是比“每个 route 里面 SQL + Python 整理 + HTML 特殊判断混在一起”更合适、同时又不会过度工程化的划分。

[1]: https://flask.palletsprojects.com/en/stable/tutorial/?utm_source=chatgpt.com "Tutorial — Flask Documentation (3.1.x)"
[2]: https://jinja.palletsprojects.com/en/stable/templates/?utm_source=chatgpt.com "Template Designer Documentation — Jinja Documentation (3.1.x)"
[3]: https://flask.palletsprojects.com/en/stable/tutorial/database/?utm_source=chatgpt.com "Define and Access the Database — Flask Documentation (3.1.x)"
[4]: https://flask.palletsprojects.com/en/stable/tutorial/templates/?utm_source=chatgpt.com "Templates — Flask Documentation (3.1.x)"
[5]: https://www.sqlite.org/lang_with.html?utm_source=chatgpt.com "The WITH Clause"
[6]: https://www.sqlite.org/windowfunctions.html?utm_source=chatgpt.com "Window Functions"
