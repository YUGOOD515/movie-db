# 远程访问

[English](../en/remote-access.md) | **简体中文**

MovieDB 默认只监听本机 `127.0.0.1`。需要在其他设备上使用时，可以用 Cloudflare Tunnel 把一个域名安全地转发到本机；再配合云端只读快照，电脑离线时也能浏览媒体库。两者都只用 Cloudflare 的免费额度。

- [Cloudflare Tunnel](#cloudflare-tunnel)
- [云端只读快照](#云端只读快照)

## Cloudflare Tunnel

前提：域名已托管在 Cloudflare，并安装了 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)。以下 `library.example.com` 换成自己的域名。

1. 创建 Tunnel 并绑定域名：

   ```sh
   cloudflared tunnel login
   cloudflared tunnel create moviedb
   cloudflared tunnel route dns moviedb library.example.com
   ```

2. 写一份配置文件（例如 `~/.cloudflared/config.yml`），把域名转发到 MovieDB 的端口：

   ```yaml
   tunnel: <Tunnel ID>
   credentials-file: <凭据 JSON 的路径>
   ingress:
     - hostname: library.example.com
       service: http://127.0.0.1:5000
     - service: http_status:404
   ```

3. 在 Cloudflare Zero Trust 中为 `library.example.com` 创建 **Access 应用**，只允许自己的邮箱（例如邮箱验证码登录）。**不要跳过这一步**，否则任何人都能访问你的媒体库。

4. 启动 MovieDB 后运行 Tunnel，两个进程都需要保持运行：

   ```sh
   cloudflared tunnel --config ~/.cloudflared/config.yml run moviedb
   ```

之后在任意设备打开 `https://library.example.com`，用获准的邮箱完成验证即可。经 Tunnel 访问时设置页只读，设置只能在本机修改。

说明：

- Cloudflare 只负责安全转发，应用、数据库、TMDB 与 AList 连接都仍在本机；电脑关机、休眠、断网或任一进程退出时网站无法使用（可配合下面的只读快照）。
- “播放”总是在运行 MovieDB 的那台电脑上打开播放器：远程点“播放”相当于遥控本机播放。
- MovieDB 使用内置的开发服务器，适合个人使用；长期无人值守运行建议改用生产 WSGI 服务器并配置开机启动。
- Tunnel 凭据保存在用户目录中，不要复制到项目或提交到 Git。

## 云端只读快照

本机离线（电脑关机、MovieDB 或 Tunnel 未运行）时，同一个域名自动切换为云端只读快照，界面与本地一致；修改类按钮置灰，写请求返回 503。本机在线时所有请求原样转发到本机，读写和播放都与原来相同。快照仍由 Cloudflare Access 保护。

### 组成

| 部分 | 作用 |
|---|---|
| `cloud/router/` | 入口 Worker（JavaScript），挂载在域名的 `/*` 路由上。先请求 Tunnel；本机的响应带 `X-MovieDB-Origin` 标记，没有标记的 5xx 或连接失败视为离线，改为返回快照 |
| `cloud/renderer/` | 只读渲染 Worker（Python Workers + Flask），复用 `app.py`、`queries/`、`viewmodels/` 和 `templates/`，渲染快照中没有预生成的页面组合 |
| R2 存储桶 `moviedb-snapshot` | 预渲染页面、`static/` 文件和 `library.db` 只读副本 |
| `scripts/publish_snapshot.py` | 备份 `library.db`，以只读模式抓取全部页面，增量上传有变化的文件 |
| `services/cloud_sync.py` | 设置 `MOVIEDB_SYNC_TOKEN` 后，应用启动时同步一次，之后 `library.db` 变化并稳定 1 分钟后自动同步（两次至少间隔 2 分钟） |

`discovery.db`、`.env` 等不会上传。离线时视频播放依赖本机 AList，因此不可用。

### 部署

1. 在 Cloudflare 创建 R2 存储桶 `moviedb-snapshot`。
2. 修改 `cloud/router/wrangler.jsonc`：
   - `routes`：换成自己的域名（例如 `library.example.com/*`），可另加一个始终显示快照的预览域名；
   - `vars.SNAPSHOT_HOSTS`：预览域名（逗号分隔）；
   - `vars.SYNC_TOKEN_SHA256`：同步令牌的 SHA-256 摘要。生成一个随机令牌写入 `.env` 的 `MOVIEDB_SYNC_TOKEN`，把它的摘要填到这里（摘要公开无妨，明文只保存在本机）。
3. 部署两个 Worker：

   ```sh
   cd cloud && npm install
   cd renderer && uv run python build.py && uv run pywrangler deploy
   cd ../router && npx wrangler deploy
   ```

4. 在 `.env` 中设置 `MOVIEDB_SYNC_TOKEN`，以及入口 Worker 的地址 `MOVIEDB_SYNC_URL`（`workers.dev` 地址），重启 MovieDB。

推送到 `main` 时 `.github/workflows/deploy-cloud.yml` 会自动重新部署渲染 Worker（修改 `templates/`、`app.py` 等代码后需要重新部署）。需要在仓库 Secrets 中配置 `CLOUDFLARE_API_TOKEN`，权限：Workers Scripts 编辑、Workers Routes 编辑、Workers R2 Storage 编辑。

### 手动同步

```sh
uv run python scripts/publish_snapshot.py            # 立即同步
uv run python scripts/publish_snapshot.py --dry-run  # 只生成，不上传
```

`MOVIEDB_SYNC_DISABLED=1` 可临时关闭自动同步。在预览域名上可以随时查看离线效果。

### 关闭

在 Cloudflare 删除域名上的 Worker 路由即可恢复为纯 Tunnel 访问。
