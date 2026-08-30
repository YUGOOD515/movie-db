技术方面采用 flask、jinja、html+css（bootstrap为基础搭建）+js。

我希望app.py较为简单，对于每个route内保有清晰的流程即可，看起来就像函数的调用链条一样，而具体的流程，可分为：
数据库查询-整理数据-前端显示；
输入检查-本地IMDB数据库查询-调用TMDB数据库API补充-录入数据。

页面排布结构

- 首页
  - 最近观看，
    - episode需要series backdrop、series名称-剧集观看到了第几季第几集-该集display title，
    - movie需要backdrop、电影名-播出年份，
    - 两个均需要观看进度条
    - 每个视频上面都有一个播放按钮，点击即可调用播放功能，所以还需要playroute
    -
  - 收藏夹，每个收藏夹显示名称、作品数量
  - 最近添加，按照added\_at展示movie、series，均为海报卡片
  - 电影类别，title\_type为movie的genres分类汇总，原始每row至多有3个genre，以逗号分割，需要路由处理，每个genre，需要选一个movie，展示该movie的backdrop，用于装饰美观
  - 剧集类别，title\_type为series的genres分类汇总，原始每row至多有3个genre，以逗号分割，需要路由处理，每个genre，需要选一个series，展示该series的backdrop，用于装饰美观
  - 未观看，playback\_progress中未有记录的movie、没有记录episode对应的series，展示海报卡片
  - 最受好评的电影：title\_type为movie，展示海报卡片（展示评分），且num\_votes不是很小的
- 最近添加、未观看、几乎完全一致，均为海报卡片陈列
- 最受好评的电影，按 IMDb 评分由高到低排列，展示前3部的简易背景板，第4-9名展示海报卡片，要评分
- 类别筛选、收藏较为类似，顶部筛选，底部展示海报卡片，其中类别筛选可以筛选 movie/series、genre、start\_year、average\_ratings、language、按照添加顺序、评分顺序、播出年份、名称（A-Z）排序
- 媒体资源管理
  - 概览
    - 展示display\_name、title\_type、推出年份、title\_id、tmdb\_id、poster、简易背景板（不展示average\_ratings）
    - 对于一个movie/series，有多个备选logo、poster、backdrop，可在此页面手动替换
    - 可按照电影、剧集筛选，也可以搜索名称/title\_id/tmdb\_id搜索
  - 关联，用户输入 alist视频直链，以及如下不同方式不同信息，即可挂削相关数据，自动入库
    - 剧集
      - 手动输入媒体类型、episode的imdb\_id/tmdb\_id，
      - 输入series名称/series imdb\_id，season number、episode number
    - 电影
      - 电影名称（通常为中文），alist视频直链
- 详情页
  - hero区
    - movie的logo、backdrop、tagline、时长、播出年份、清晰度、编码方式、hdr杜比等特殊格式多个陈列（如有）、genres陈列、overview、播放、收藏、播出年份、average\_ratings，如果有playback\_progress，需要 watched\_at最新一条的 position\_ms，转化显示在播放按钮上
    - series，点开默认为S1E1的信息页，没有tagline，有 episode 的 display\_title、series logo、episode backdrop、episode 时长、series 播出年份、清晰度、编码方式、hdr杜比等特殊格式多个陈列（如有）、genres陈列、overview、播放、收藏(收藏该 episode）、播出年份、、average\_ratings；如果有playback\_progress，采用最新一条，需要该series watched\_at最新一条的 position\_ms，转化显示在播放按钮上
  - series的选集区，movie无，上方为季选择区，下方为当前选中季的每一集，每一集有 episode backdrop、season number、episode number、时长、display\_title、观看进度条
  - 元数据，来自于title\_basic、media\_file的陈列

相关常用语解释：
播出年份：movie为start\_year，series展示为start\_year-end\_year，例如2024-2026
展示 poster：除了剧集详情页，均为movie poster，episode对应的series poster
海报卡片：poster展示、display\_title、title\_type、播出年份、average\_ratings（通常不展示）
简易背景板：movie为logo、backdrop，series为logo、backdrop，都有average\_ratings

