# MovieDB

本地 Flask 影视资源管理网页。页面查询只读 `library.db`，写操作集中在 `models/library.py`；TMDB、AList、ffprobe 与 MPC-HC 接口位于 `connectors/`。

媒体管理页支持使用 IMDb Title ID 录入电影或单集。Series Title ID 需要同时提供季号和集号；Episode Title ID 会自动解析父剧集。TMDB 提供标题、年份、分类、季集结构与单集 IMDb ID、简介、演员和图片；IMDb 评分取自官方评分文件 `title.ratings.tsv.gz`（约 9 MB，首次录入时自动下载到 `imdb/`，超过一天自动更新，可用 `IMDB_RATINGS_PATH` 指定位置）。媒体管理页的“刷新评分”同时用该文件更新全部作品和单集的 IMDb 评分。不再需要本地 `imdb.db`。

中文名按 TMDB zh-CN → zh-TW → zh-HK 回退；分类转换为原有的 IMDb 英文分类（如 Sci-Fi & Fantasy → Sci-Fi, Fantasy）。TMDB 上没有 IMDb ID 的单集无法录入。重新录入已有作品时，库内已有的名称、分类和季集关系保持不变（只替换“第 N 集”这类占位名），评分取不到时保留旧值。

## 本地配置

`uv run flask --app app run` 会通过 python-dotenv 自动读取项目根目录的 `.env`（变量名参考 `.env.example`）；也可以在 PowerShell 中直接设置环境变量：

```powershell
$env:FLASK_SECRET_KEY = "生成的随机密钥"
$env:ALIST_DIR = "C:\alist"
$env:ALIST_AUTOSTART = "1"
$env:ALIST_ORIGIN = "http://127.0.0.1:5244"
$env:ALIST_USERNAME = "AList 用户名"
$env:ALIST_PASSWORD = "AList 密码"
$env:TMDB_TOKEN = "TMDB API Read Access Token"
```

安装依赖并启动：

```powershell
uv sync
uv run flask --app app run
```

配置 `ALIST_DIR` 后，应用启动时会先检查 `ALIST_ORIGIN`。如果 AList 尚未运行，应用会在该目录执行 `alist.exe start`，并等待服务可用；已运行的实例不会重复启动。未配置 `ALIST_DIR`，或将 `ALIST_AUTOSTART` 设为 `0` 时，保持原来的手动启动方式。AList 作为独立后台进程运行，Flask 退出时不会自动停止它。

TMDB 与 AList 凭据不得写入源码或提交到 Git。曾经进入 Git 历史的凭据应在对应服务中轮换。

## 按名称关联媒体

关联页保留手动逐条填写 IMDb ID、季集号及 AList 链接，以及 JSON 上传两种方式，入口在“待关联”右上角的“⋯”菜单（手动录入、上传 JSON、JSON 模板）。“手动录入”面板中的“通过 TMDB 搜索添加”提供另一条完整流程：输入名称搜索 → 选择电影或剧集 → 剧集按行输入季号、集号并填写 AList 视频直链，可增删多条单集 → 全部添加到待导入列表。电影仍填写一条链接后添加。季号、集号使用与手动录入一致的数字输入框，导入时按 TMDB 季集校验。添加仅生成页面记录，不覆盖已有内容；首次添加会替换唯一的全空占位行，最后统一点击“校验并批量录入”。

点开待关联条目进入关联界面：顶部作品卡片显示海报、年份、类型和季集数，“TMDb↗”打开 TMDB 页面核对，“更换”重新搜索作品。剧集的文件表每行显示季号/集号与对应的 TMDB 单集，问题行（待填写、无此集、需检查）以黄色标出，可点“只看问题”筛选，也可直接从“对应”下拉选择单集；同一集的多个文件按多版本关联。季集识别规则默认收起，只在有文件读不出集号时自动展开；规则支持拖动匹配块排序，也可聚焦匹配块后按 Alt 加左右方向键调整。底部操作栏实时显示是否全部对上，没有问题后点“关联 N 个”一步完成（大批量自动分批），完成后返回列表。

详情页底部提供“删除作品”：打开弹窗后检查并展示作品简介、评分、全部季集、本地图片和观看记录，用户确认后执行。图片与演员照片支持横向浏览，以标签区分删除与共用保留；观看记录显示观看时间、播放位置与文件总时长，无记录时隐藏。AList 原视频和收藏夹本身保留。确认范围变化或超过五分钟时需要重新检查；删除成功后返回媒体资源管理页，并显示底部提醒。

搜索及 IMDb 映射在进程内缓存 10 分钟（最多 128 项），相同并发请求合并。选中的对应关系可在导入时复用，缓存过期或进程重启后按原流程重新查询。演员和正式图片在导入时才获取；候选海报通过浏览器按需加载。TMDB 不可用时可直接填写 IMDb ID；缺少 IMDb 映射或本地数据时会提示原因。原有 JSON 导入及整批失败回滚行为保持不变。

## 智能识别待关联

“待关联”页扫描用户选定的 AList 目录（点“+”浏览并添加，未选择时沿用自动查找的“来自：分享”目录），在后台清洗伪装文件名、搜索 TMDB 并推测季集。结果按标签页分为三组，标签上显示数量，当前组处理完后自动切到下一组：

- **可确认**：置信度 ≥ 0.8，剧集的每个文件都对上 TMDB 中唯一的单集。同一作品的多个来源合并为一行，可展开查看。各行默认勾选，底部操作栏显示已选项数和视频数，点“确认关联 N 项”批量入库；仍需人工点击，不会自动入库。
- **待核对**：行内标签说明原因（如“6/12 集对上”“与可确认项重复”“有同名作品”“把握不大”），点“核对”打开已预填识别结果的关联界面。
- **未识别**：规则与 AI 都没有可靠结果；判断为非影视内容（课程、软件等）时标为“可能不是影视”，可逐项或底部“全部忽略”。

每行的“⋯”菜单提供更换作品、重新识别和忽略。

识别顺序为：库内已有关联 → 曾确认过的同名目录 → 库内同名作品 → TMDB 规则搜索 → AI。配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 后，规则得分低于 0.75 的目录才会请求 AI；AI 给出的候选仍回到 TMDB 校验，置信度最高 0.9。`LLM_API` 选择 `chat`（`/chat/completions`）或 `responses`（`/responses`），不支持 JSON 模式的兼容服务会自动退回纯提示。

确认关联后记住原目录名，下次同名目录直接识别。忽略的条目收在“已忽略”标签页，可随时恢复。识别结果按目录内容签名缓存，内容未变不会重复识别。扫描时顺带检查已关联文件：AList 明确返回不存在才标记为“失效”，网络错误不改变状态；媒体资源管理页可按“失效”筛选，源文件不会被删除。

以上数据存放在 `discovery.db`（与 `library.db` 同目录，可用 `MOVIEDB_DISCOVERY_DATABASE` 指定），不进入云端快照。配置 AList 账号后应用启动时自动扫描一次，`MOVIEDB_AUTOSCAN=0` 可关闭。

## 《绝命毒师》本地 API 测试

本地回放服务器只提供《绝命毒师》（IMDb `tt0903747` / TMDb `1396`）的中文 TMDb 数据和 MDBList 评分。搜索支持简繁中文、英文及部分名称；支持详情、`append_to_response`、季集资料、IMDb 映射，以及当前搜索和导入使用的图片。未收录的内容返回空结果或 404，服务器不请求远程 API。

快照位于 `local-test-data/breaking-bad/`，独立于业务库和 `static/images`，删除网站里的作品不会删除快照。包含真实 API 响应、当前相关的业务库记录、已有剧照/海报/背景/标志/演员照片，以及图片 URL 映射和 SHA-256 清单。单集 find 响应由保存的 TMDb 季资料与单集 external_ids 组合；旧快照没有译名时按“无译名”回放。仅保存当前搜索、导入使用的图片及原有文件；不复制 AList 视频。该目录被 Git 忽略。

先在一个 PowerShell 窗口运行本地 API：

```powershell
uv run python scripts/local_test_api.py
```

停止原有 MovieDB 进程，再在启动 MovieDB 的 PowerShell 窗口中替换三个 URL（图片使用独立地址）：

```powershell
$env:TMDB_API_ORIGIN = "http://127.0.0.1:5001/3"
$env:TMDB_IMAGE_ORIGIN = "http://127.0.0.1:5001/t/p"
$env:MDBLIST_API_URL = "http://127.0.0.1:5001/mdblist/api/"
uv run flask --app app run --host 127.0.0.1 --port 5000
```

原有 Token/API Key 可保留；本地服务器忽略凭据。没有凭据时可将 `TMDB_TOKEN` 和 `MDBLIST_APIKEY` 设为 `local-test`。AList 流程照常运行；IMDb 评分文件仍从官方地址下载。环境变量需要设置在实际启动 MovieDB 的进程中，配置后须重启应用以清空旧缓存；只编辑 `.env` 不会替换 TMDb URL。恢复远程服务时移除这三个环境变量并重启应用。

如需重新抓取，执行 `uv run python scripts/capture_breaking_bad.py --output local-test-data/breaking-bad-new`，再用 `--data local-test-data/breaking-bad-new` 启动回放服务器。抓取会访问远程 API，但不会写业务库；已有快照目录禁止覆盖。

## Cloudflare 远程访问

Cloudflare Tunnel 已将 `library.yu2yu2.dpdns.org` 转发至本机 `127.0.0.1:5000`，并由 Cloudflare Access 限制访问。以后无需重复配置 Cloudflare；开机后分别启动 MovieDB 和 Tunnel 即可。

先在一个 PowerShell 窗口启动 MovieDB：

```powershell
cd 'C:\YUFiles\DFiles\Self-Knowledge\Design\2026-08-26-movieDB'

uv run flask --app app run --host 127.0.0.1 --port 5000
```

再在另一个 PowerShell 窗口启动 Tunnel：

```powershell
& "$env:LOCALAPPDATA\Programs\cloudflared\cloudflared.exe" `
  tunnel `
  --config "$env:USERPROFILE\.cloudflared-yu2yu2\config.yml" `
  run b6a1b016-2219-4c46-85a3-10ab8bb3c123
```

两个窗口都需要保持运行，停止时分别按 `Ctrl+C`。启动完成后，在任意设备打开 [https://library.yu2yu2.dpdns.org](https://library.yu2yu2.dpdns.org)，使用 Cloudflare Access 中获准的邮箱完成验证码登录。

Cloudflare 只提供安全转发，应用、SQLite 数据库、TMDB 和 AList 连接仍运行在本机；电脑关机、休眠、断网或任一进程退出时，网站将无法使用。当前命令使用 Flask 开发服务器，适合个人临时运行；长期无人值守运行应改用生产 WSGI 服务器并配置开机启动。

Tunnel 凭据保存在用户目录中，不得复制到项目或提交到 Git：

```text
%USERPROFILE%\.cloudflared-yu2yu2\
```


## 云端只读快照

本机离线（电脑关机、Flask 或 Tunnel 未运行）时，`library.yu2yu2.dpdns.org` 自动切换为云端只读快照，界面与本地一致；修改类按钮置灰，写请求返回 503。本机在线时所有请求原样转发到本机，读写和播放都与原来相同。全部使用 Cloudflare 免费额度，仍由 Cloudflare Access 保护。

- `cloud/router/`：入口 Worker（JavaScript），挂载在 `library.yu2yu2.dpdns.org/*`。先请求 Tunnel；本机响应带 `X-MovieDB-Origin` 标记，没有标记的 5xx 或连接失败视为离线。
- `cloud/renderer/`：只读渲染 Worker（Python Workers + Flask），复用 `app.py`、`queries/`、`viewmodels/` 和 `templates/`，渲染快照中没有预生成的页面组合。
- R2 存储桶 `moviedb-snapshot`：预渲染页面、`static/` 文件和 `library.db` 只读副本。
- `scripts/publish_snapshot.py`：备份 `library.db`，以只读模式抓取全部页面，增量上传变化的文件。
- `services/cloud_sync.py`：`.env` 设置 `MOVIEDB_SYNC_TOKEN` 后，应用启动时同步一次，之后 `library.db` 变化并稳定 1 分钟后自动同步（两次同步至少间隔 2 分钟）。

手动同步或预览：

```powershell
uv run python scripts/publish_snapshot.py            # 立即同步
uv run python scripts/publish_snapshot.py --dry-run  # 只生成，不上传
```

[https://readonly-v1.yu2yu2.dpdns.org](https://readonly-v1.yu2yu2.dpdns.org) 始终显示快照（同样受 Cloudflare Access 保护），可在本机在线时检查离线效果。

离线时视频播放依赖本机 AList，因此不可用；在线时可远程播放。修改 `templates/`、`app.py` 等代码后需重新部署渲染 Worker，`.github/workflows/deploy-cloud.yml` 会在推送到 `main` 时自动部署（需在仓库 Secrets 配置 `CLOUDFLARE_API_TOKEN`，权限：Workers Scripts 编辑、Workers Routes 编辑、Workers R2 Storage 编辑）。手动部署：

```powershell
cd cloud; npm install
cd renderer; uv run python build.py; uv run pywrangler deploy
cd ..\router; npx wrangler deploy
```

关闭云端快照：在 Cloudflare 删除 `library.yu2yu2.dpdns.org/*` Worker 路由即可恢复为纯 Tunnel 访问。

<!-- archive-preparation:2026-10-14:start -->
## 界面动效

- 首页顶部为「最新入库」精选轮播（Embla），背景缓慢推镜，页面环境色随当前影片变化。
- 海报 / 背景图先显示 BlurHash 模糊占位，再淡入原图；色调表 `static/thumbnails/tones.json` 在录入时生成，启动时自动为旧图片补齐（设置 `MOVIEDB_TONES_DISABLED=1` 可跳过）。
- 桌面端海报悬停倾斜、列表进入视口时依次浮现、卡片到详情页的共享元素转场（View Transitions，Chrome / Edge / Safari 18+）。系统开启「减少动态效果」时自动关闭。
- 第三方库（Embla Carousel、Motion、vanilla-tilt、BlurHash，均为 MIT）随仓库放在 `static/vendor/`。

## 使用与归档说明（2026-10-14）

Flask + Jinja 影视库：TMDB 主数据、IMDb / MDBList 评分、AList 媒体来源及 MPC-HC 播放。读查询、写模型、连接器、服务编排和页面 ViewModel 分层保存。

### 结构与入口

- [app.py](app.py)：Flask 路由。
- [queries](queries)：只读查询。
- [models/library.py](models/library.py)：业务写操作。
- [connectors](connectors)：外部接口与本地播放器。
- [services](services)：导入、关联、播放和删除业务。
- [pyproject.toml](pyproject.toml)：Python 3.13+ 依赖。

### 使用前提

先阅读原 README 的配置与数据准备，执行 uv sync 后，在自己的本地环境启动 uv run flask --app app run --host 127.0.0.1 --port 5000。需要自己的业务库、TMDB / AList 配置，播放器和 ffprobe 按功能选配。仅克隆源码不等于获得作者的数据库或媒体。

### 归档分工

**GitHub：** 当前源码、迁移、测试、锁文件、脱敏示例和运行必需图标；优先从当前工作树生成经审核的干净代码快照，避免直接推送含历史凭据 / 数据的旧仓库。

**夸克网盘：** library.db、个人图片、备份与媒体清单只作一致性、加密私人备份。IMDb 评分文件由程序从官方地址自动下载，不需要备份或分享。

### 当前限制

旧 helpers.py 的历史抽样仍命中凭据模式，library.db 出现在历史；data/white_album_2_import.json 和 static/templates/media_import_template.json 含签名 URL，发布前必须替换为虚构链接。

本次仅完成本地结构、关键入口与依赖检查，以及 README 准备，未验证全部运行功能。本轮未创建 GitHub 仓库或夸克分享链接，也未上传本次归档；后续归档须经目录所有者批准。

作者自有内容尚未统一选择开源许可证；第三方源码和资源沿用原许可，目录内出现的第三方许可证不自动覆盖其他文件。
<!-- archive-preparation:2026-10-14:end -->
