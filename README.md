# MovieDB

本地 Flask 影视资源管理网页。页面查询只读 `library.db`，写操作集中在 `models/library.py`；TMDB、AList、ffprobe 与 MPC-HC 接口位于 `connectors/`。

媒体管理页支持使用 IMDb Title ID 录入电影或单集。Series Title ID 需要同时提供季号和集号；Episode Title ID 会自动解析父剧集。IMDb 提供身份、层级、标题及评分，TMDB 只补充简介、标语、原始语言和图片。

## 本地配置

项目不会自动读取 `.env`。启动前请在 PowerShell 中设置环境变量；变量名可参考 `.env.example`：

```powershell
$env:FLASK_SECRET_KEY = "生成的随机密钥"
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

TMDB 与 AList 凭据不得写入源码或提交到 Git。曾经进入 Git 历史的凭据应在对应服务中轮换。
