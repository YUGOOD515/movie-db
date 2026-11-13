# HTTP 接口

所有路由都在 `app.py`。本文先说明各接口共同遵守的约定，再按功能列出路由；参数和返回字段以代码为准。

## 约定

### 页面与接口

- 页面（GET，返回 HTML）：查询 → viewmodel → `render_template`。
- 表单提交（POST，浏览器原生表单）：处理后用 `flash` 写提示，再重定向回 `return_to`（只接受站内相对路径，见 `_safe_return_url`）。
- 脚本调用（`fetch`，返回 JSON）：页面脚本发起，前端根据返回内容更新界面。

### JSON 错误格式

目前有两种格式，新接口沿用所在分组的格式：

| 分组 | 成功 | 失败 |
|---|---|---|
| 设置、首次引导、网盘（`/setup/*`、`/settings/*`、`/manager/drives/*`） | `{ok: true, ...}` | `{ok: false, message}`；设置校验失败另带 `errors` |
| 资源管理、作品（`/manager/*`、`/title/*`） | 直接返回数据 | `{error}` |

### 状态码

| 状态码 | 含义 |
|---|---|
| 400 | 请求参数或数据无效 |
| 403 | 请求来源不正确，或该操作只能在本机执行 |
| 404 | 对象不存在；或在只读快照、远程访问中不提供 |
| 409 | 状态已变化（例如删除确认后作品又被改动） |
| 413 | 请求数据过大（批量 JSON 上限 1 MiB） |
| 502 | 外部服务（TMDB、AI）暂时无法访问 |
| 503 | 外部服务未配置；或只读快照不支持该操作 |

### 安全

- 写请求必须来自本站页面（`guard_request_source` 检查 `Origin` / `Referer` / `Sec-Fetch-Site`），用来防止跨站请求伪造。
- 设置了 `MOVIEDB_ALLOWED_HOSTS` 时，其他 Host 一律拒绝（防 DNS 重绑定）。
- 会写 `.env` 或网盘凭据的接口先调用 `_setup_guard`：只读快照返回 404，经 Cloudflare Tunnel 的请求返回 403。新增这类接口也要先调用它。
- 只读快照模式只放行 `READ_ONLY_ENDPOINTS` 中的 GET 端点。

### 轮询

扫描状态、任务列表、任务详情等会被页面轮询的 GET 接口返回 ETag；内容没有变化时回 304，减少传输和整表重绘。新增轮询接口照此处理。

## 路由

### 浏览（只读快照可用）

| 方法 | 路径 | 返回 | 说明 |
|---|---|---|---|
| GET | `/` | HTML | 首页：精选轮播与各类横向轨道 |
| GET | `/recent` · `/unwatched` · `/top` | HTML | 最近添加 / 未观看 / 高分电影 |
| GET | `/catalog` | HTML | 分类筛选，参数见 `parse_catalog_filters` |
| GET | `/favorites` · `/collection/<id>` | HTML | 收藏 |
| GET | `/title/<title_id>` | HTML | 详情页；`season`、`episode` 选集，`resume=1` 定位到续播集 |
| GET | `/title/<title_id>/episode-panel` | HTML 片段 | 单集面板 |
| GET | `/media` | HTML | 资源管理；`tab=overview\|associate\|drives`，`type`、`q`、`health=missing` |

### 播放与收藏

| 方法 | 路径 | 返回 | 说明 |
|---|---|---|---|
| POST | `/play/<file_id>` | 204 | 用本机播放器打开 `/stream` 地址，并回写播放进度 |
| POST | `/title/<title_id>/resume` | 307 → `/play/<file_id>` | 剧集续播 |
| GET/HEAD | `/stream/<token>/<name>` | 媒体流 | 播放器和 ffprobe 读取媒体，支持 Range |
| POST | `/collection/<id>/<title_id>/toggle` | 重定向 | 加入或取消收藏 |

### 关联与导入

| 方法 | 路径 | 返回 | 说明 |
|---|---|---|---|
| GET/POST | `/manager/discovery` | JSON | 扫描状态 / 开始扫描（`refresh=1` 全量） |
| POST | `/manager/discovery/roots` | JSON | 添加或移除扫描目录（`action=remove`） |
| GET | `/manager/discovery/browse` | JSON | 浏览网盘目录 |
| POST | `/manager/discovery/ignore` | JSON | 忽略或恢复某条扫描结果 |
| POST | `/manager/discovery/recognize` | JSON | 识别单条扫描结果（规则 + AI） |
| GET | `/manager/titles/search` | JSON | 搜索 TMDB |
| GET | `/manager/titles/select` | JSON | 选定 TMDB 作品，取 IMDb 映射 |
| GET | `/manager/titles/poster` | 重定向 | 搜索结果的中文海报 |
| GET | `/manager/titles/episodes` | JSON | 作品的季集资料 |
| GET/POST | `/manager/association/jobs` | JSON（POST 返回 202） | 查询 / 提交 / 取消（`cancel`）/ 重试（`retry`）关联任务 |
| POST | `/manager/import/batch` | 重定向 | 表单或 JSON 文件批量录入，全部成功才写入 |
| GET | `/manager/tasks` · `/manager/tasks/detail?id=` | JSON | 后台任务列表与详情 |
| GET/POST | `/manager/ratings/refresh` | JSON | 评分刷新状态 / 开始刷新 |
| POST | `/manager/<title_id>/artwork` | 重定向 | 设置主海报、背景、Logo |
| GET/POST | `/title/<title_id>/deletion` | JSON | 删除预览（返回确认 token 和 HTML）/ 凭 token 执行删除 |

### 网盘（本机限定）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/manager/drives/save` | 新建或修改挂载 |
| POST | `/manager/drives/<mount>/test` | 检测连接 |
| POST | `/manager/drives/<mount>/toggle` | 启用 / 停用 |
| POST | `/manager/drives/<mount>/delegate` · `/reclaim` | 改由透传提供 / 收回 |
| POST | `/manager/drives/<mount>/delete` | 删除挂载 |
| POST | `/manager/drives/qr-login` | 扫码登录（夸克、UC、阿里、百度、115）：`action=start` 返回二维码与登录编号，`poll` 查询；确认后服务端直接保存挂载，令牌不返回页面 |
| POST | `/manager/drives/oauth` | 网页授权完成（OneDrive、Google Drive）：`url` 为授权结束后的网址或其 # 部分，解码出令牌后保存挂载 |
| POST | `/manager/drives/import-alist` | 从 AList 的 `data.db` / 备份 JSON 导入：`action=preview` 列出处理方式，`apply` 导入所选挂载 |

改变路由的操作返回 `rerouted: true` 时，服务端会在后台重新扫描。

### 设置与首次引导（本机限定）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/setup` | 首次引导页；媒体库建好后跳转到 `/settings` |
| GET | `/setup/status` · `/settings/state` | 当前配置与检测状态 |
| POST | `/setup/test/<service>` · `/settings/test/<service>` | 检测某项服务 |
| POST | `/setup/save` · `/settings/save` | 保存配置，写入 `.env` 并立即生效 |
| GET/POST | `/settings/imdb-episodes` | 本机单集索引状态 / 后台检查更新；GET 支持 ETag，POST 新任务返回 202，重复任务返回 200；返回 `{ok, started, index}`，更新失败仍可使用旧索引 |
| POST | `/settings/clear` | 清除本机数据，需输入确认词 |
| POST | `/settings/restart` | 桌面版重启 |
| POST | `/desktop/show` | 桌面启动器专用（需 `X-MovieDB-Desktop: 1`） |
