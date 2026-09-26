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
