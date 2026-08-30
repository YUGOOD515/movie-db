# library.db 读取链路重构计划

## 目标

以当前 `library.db` 和 `query.md` 为准，完成“数据库查询 → ViewModel 整理 → Jinja/Bootstrap 显示”的读取链路，并覆盖首页、列表/筛选、收藏、详情、媒体管理和播放功能。保留现有录入链路的职责边界，不在本阶段迁移数据库数据。

## 阶段

- [x] 1. 核对实际数据库结构、数据质量、现有路由、模板和测试基线
- [x] 2. 建立薄 `db.py`、页面级 `queries/` 与通用 `viewmodels/`
- [x] 3. 重写读取型 route，保持 `app.py` 为清晰调用链
- [x] 4. 调整 Jinja 组件与页面，复用海报卡片、背景板、进度和媒体标签
- [x] 5. 实现并保护 `/play/<file_id>` 播放链路
- [x] 6. 增补测试，执行编译、单测、数据库、路由、静态资源和差异检查
- [x] 7. 按 6/3/2 对齐线恢复首页横向轨道，并修正最近观看卡片层级与定位
- [x] 8. 对照 Git 基线逐页映射旧详情、筛选、收藏和媒体管理 DOM/样式与新版数据契约
- [x] 9. 以旧模板为基线迁移四类页面，保留新版 query/viewmodel/route 分层
- [x] 10. 执行路由、模板、交互、静态资源和数据库不变性回归验证

## 约束与决策

- 当前 `library.db` 是运行时事实来源；先检查真实 schema 与数据，再编写查询。
- `queries/` 只查询并返回数据库行；`viewmodels/` 负责年份、进度、剧集父级图片、badge 等页面形状；Jinja 不承担业务判断。
- 读取端统一使用 `title_id`，详情入口为 `/title/<title_id>`；必要时保留旧 URL 的兼容跳转。
- 类别筛选使用一个 `/catalog` route；详情页 movie/series 共用一个模板。
- 播放使用 POST route；在确认现有播放器调用方式前不假设外部程序。
- 前端延续现有 Bootstrap 风格，不使用 `design-taste-frontend`。
- 不修改用户已导入的数据，不执行 schema 迁移。
- 本轮只完成读取/整理/显示链路；管理页关联表单保留入口但禁用提交，旧 `movie.db` 写入函数不接到 `library.db`。
- 二次视觉迁移以 Git 基线页面为权威：保留旧 DOM 层级、组件类名和排版，只改字段访问、URL、表单参数及必要的数据适配。

## 错误记录

- 追加新库 mutation 与重写 `app.py` 的组合补丁因 `models.py` 末尾上下文不匹配而整体未应用；改为先定位文件尾，再拆分补丁。
- 替换 `app.py` 时同一补丁同时删除和新增同一路径不被工具接受；拆为两个原子补丁。
- 首次 GET 冒烟测试发现最近观看 ViewModel 未显式提供 `rating`，而通用背景卡读取该键；补为 `None`，保持卡片统一契约。
- 第二次 GET 冒烟测试发现 Jinja 的 `page.items` 优先解析为字典方法；所有页面改用 `page["items"]` 显式取列表。
- 首轮正式测试在 Windows 清理临时库时发现新 mutation 连接未显式关闭；SQLite 上下文只提交事务，改为 `closing(connection), connection` 双上下文。
- 收尾时最初用旧草案的列清单核对新 schema，产生 `schema_columns_match=False`；改为逐表对比建库脚本生成结构与正式库真实结构，七表字段全部一致。
- 首次逐表对比命令因 PowerShell 内嵌 Python 引号解析失败；依照项目惯例改用 UTF-8 here-string 管道执行。
- Windows 下调用 Playwright 的 Bash 包装脚本时进入 WSL，无法识别 Windows 技能路径；改用同等的 `npx --package @playwright/cli playwright-cli` 入口。
- 首次补入 `continue-visual` 后浏览器仍显示旧位置，原因是无重载模式的 Flask 进程缓存了旧模板；重启本地验证进程后新 DOM 和定位生效。
- 收藏 query 与 ViewModel 的组合补丁误把两个文件上下文放入同一 `Update File`，校验失败且未产生修改；拆成独立文件补丁继续。
- 首次全套测试仍断言新版详情文案 `S01E01/继续 S03E13`，旧详情恢复后实际语义为 `S1 · E1` 和当前选集面板；按新的“旧 UI 基线”更新布局契约测试。
- 最终只读核验发现正式 `library.db` 哈希在 12:49:50 发生变化；测试目标配置为临时副本且未授权回退，先与 Git blob 做逐表语义对比，再决定是否存在真实数据写入。
