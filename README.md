# MovieDB

本地 Flask 影视资源管理网页。运行时使用只读 `library.db` 查询数据，写操作集中在 `models.py`。

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
