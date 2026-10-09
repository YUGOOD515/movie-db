# MovieDB

本地 Flask 影视资源管理网页。页面查询只读 `library.db`，写操作集中在 `models/library.py`；TMDB、AList、ffprobe 与 MPC-HC 接口位于 `connectors/`。

媒体管理页支持使用 IMDb Title ID 录入电影或单集。Series Title ID 需要同时提供季号和集号；Episode Title ID 会自动解析父剧集。IMDb 提供身份、层级、标题及评分，TMDB 补充简介、标语、原始语言、演员和图片。

## 本地配置

项目不会自动读取 `.env`。启动前请在 PowerShell 中设置环境变量；变量名可参考 `.env.example`：

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

关联页保留手动逐条填写 IMDb ID、季集号及 AList 链接，以及 JSON 上传两种方式。“手动录入 / JSON 导入”中的“通过 TMDB 搜索添加”提供另一条完整流程：输入名称搜索 → 选择电影或剧集 → 剧集按行输入季号、集号并填写 AList 视频直链，可增删多条单集 → 全部添加到待导入列表。电影仍填写一条链接后添加。季号、集号使用与手动录入一致的数字输入框，导入时沿用本地 IMDb 校验。添加仅生成页面记录，不覆盖已有内容；首次添加会替换唯一的全空占位行，最后统一点击“校验并批量录入”。

待关联内容在匹配界面通过任务标题旁的“重新搜索匹配”更换作品。作品卡片按季横向列出 IMDb 集名，文件名规则支持拖动匹配块排序，也可聚焦匹配块后按 Alt 加左右方向键调整。扫描、检查和完成关联的结果统一使用底部提示，成功、警告和失败以颜色区分。

详情页底部提供“删除作品”：打开弹窗后检查并展示作品简介、评分、全部季集、本地图片和观看记录，用户确认后执行。图片与演员照片支持横向浏览，以标签区分删除与共用保留；观看记录显示观看时间、播放位置与文件总时长，无记录时隐藏。AList 原视频、IMDb 主数据库和收藏夹本身保留。确认范围变化或超过五分钟时需要重新检查；删除成功后返回媒体资源管理页，并显示底部提醒。

搜索及 IMDb 映射在进程内缓存 10 分钟（最多 128 项），相同并发请求合并。选中的对应关系可在导入时复用，缓存过期或进程重启后按原流程重新查询。演员和正式图片在导入时才获取；候选海报通过浏览器按需加载。TMDB 不可用时可直接填写 IMDb ID；缺少 IMDb 映射或本地数据时会提示原因。原有 JSON 导入及整批失败回滚行为保持不变。

## 《绝命毒师》本地 API 测试

本地回放服务器只提供《绝命毒师》（IMDb `tt0903747` / TMDb `1396`）的中文 TMDb 数据和 MDBList 评分。搜索支持简繁中文、英文及部分名称；支持详情、`append_to_response`、季集资料、IMDb 映射，以及当前搜索和导入使用的图片。未收录的内容返回空结果或 404，服务器不请求远程 API。

快照位于 `local-test-data/breaking-bad/`，独立于业务库和 `static/images`，删除网站里的作品不会删除快照。包含真实 API 响应、当前相关的 IMDb/业务库记录、已有剧照/海报/背景/标志/演员照片，以及图片 URL 映射和 SHA-256 清单。单集 find 和 external_ids 响应由保存的 TMDb 季资料与本地 IMDb 对应关系组合。仅保存当前搜索、导入使用的图片及原有文件；不复制 AList 视频。该目录被 Git 忽略。

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

原有 Token/API Key 可保留；本地服务器忽略凭据。没有凭据时可将 `TMDB_TOKEN` 和 `MDBLIST_APIKEY` 设为 `local-test`。AList 和本地 IMDb 流程照常运行。环境变量需要设置在实际启动 MovieDB 的进程中，配置后须重启应用以清空旧缓存；只编辑 `.env` 不会替换 TMDb URL。恢复远程服务时移除这三个环境变量并重启应用。

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
