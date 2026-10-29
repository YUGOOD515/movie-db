# 网盘层规格与 OpenList 对照

> 配合 `task_plan.md` 使用。本文件只写「怎么请求、怎么解析、怎么出错」；结构、接口、阶段以 `task_plan.md` 为准。
> 参照源码：OpenList v4.2.9（`fb692b5`），路径均相对 https://github.com/OpenListTeam/OpenList 。
> 约定：下文「失败」= 抛 `DriveError`（中文、无凭据）；「凭据失效」= 抛 `AuthError`；「限流」= 交给 `throttle.call(..., throttled=...)` 按 429 处理（退避重试，次数由策略的 `attempts` 决定，驱动里**不要**自己写重试循环，令牌刷新那一次重试除外）。

## 通用规则（所有驱动）

- 时间：统一转成带时区的 ISO 8601 字符串填 `Entry.modified` / `created`。
- 名称：驱动返回的 `name` 原样使用（夸克需先 `html.unescape`）；`Entry.id` 用字符串。
- 分页：`list()` 必须翻完全部分页再返回；单目录最多 10000 项，超出报「目录文件过多」。
- `check()`：成功返回一句给用户看的话；凭据问题抛 `AuthError`，其余抛 `DriveError`。
- 令牌刷新：同一驱动实例内用锁保证同时只刷新一次；刷新成功后**立即** `save(新config)`。
- 限流策略（`connectors/throttle.py`，`LABELS` 同步补中文名）：

| service | 用于 | rate / 秒 | concurrency | 来源 |
|---|---|---|---|---|
| `quark` | 夸克 / UC 全部接口 | `DRIVE_RATE`（默认 20） | `DRIVE_CONCURRENCY`（默认 4） | OpenList 无限速，沿用旧 AList 值 |
| `baidu` | 百度全部接口 | 同上 | 同上 | 同上 |
| `alist` | AList 透传 | 同上 | 同上 | 原有策略改为读 `DRIVE_*` |
| `aliyun-list` | 阿里 `openFile/list` | 3.9 | 2 | `drivers/aliyundrive_open/limiter.go` `listRateLimit` |
| `aliyun-link` | 阿里 `getDownloadUrl` | 0.9 | 1 | `linkRateLimit` |
| `aliyun` | 阿里其余接口 | 14.9 | 2 | `otherRateLimit` |
| `pan123-list` | 123 `file/list` | 3 | 2 | `drivers/123_open/util.go` `InitApiInfo` QPS 表 |
| `pan123-link` | 123 `download_info` | 5 | 2 | 同上 |
| `pan123` | 123 其余接口（含 user/info、access_token） | 1 | 1 | 同上 |

- concurrency 列与 `quark` / `baidu` / `alist` 的取值为本项目设定（OpenList 只限速率）。令牌续期服务 `api.oplist.org` 的请求走对应驱动的「其余接口」策略（夸克、百度为各自的 service）。
- `/stream` 转发**文件数据**的请求不经过 `throttle`（只在 `media_stream` 中按 `Link.concurrency` 控制并发）；取链、列目录等 API 请求必须经过。

---

## A.1 夸克 / UC（`connectors/drives/quark.py`，参照 `drivers/quark_uc`）

一个类 `QuarkDrive`，`kind` 分别为 `quark`、`uc`，只是常量不同（在 `DRIVERS` 里注册两次，或用两个极小子类）：

| 常量 | 夸克 | UC |
|---|---|---|
| API | `https://drive.quark.cn/1/clouddrive` | `https://pc-api.uc.cn/1/clouddrive` |
| Referer | `https://pan.quark.cn` | `https://drive.uc.cn` |
| `pr` | `ucpro` | `UCBrowser` |
| UA | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch` | 同左，把 `quark-cloud-drive` 换成 `uc-cloud-drive` |

字段：`cookie`（secret，必填，提示「浏览器登录网页版后复制完整 Cookie」）、`root_id`（高级，默认 `0`）。`root_id` 覆盖 `root_id`。

**请求**：每个请求带 query `pr=<pr>&fr=pc`，headers `Cookie`、`Accept: application/json, text/plain, */*`、`Referer`。响应 JSON `{status, code, message, data, metadata}`：`status >= 400` 或 `code != 0` 即失败。

- 响应头有 `Set-Cookie: __puus=...` 时，把 cookie 字符串里的 `__puus` 替换（没有就追加）为新值并 `save()`（OpenList `util.go` 的 `request`）。
- 凭据失效（本项目规则，OpenList 未区分）：HTTP 401，或 `message` 含「登录」「login」「token」→ `AuthError("夸克 Cookie 已失效，请重新复制")`。
- 限流：`message` 命中现有正则 `频繁|过快|稍后再试|too many|rate.?limit|\b429\b`（从 `connectors/alist.py` 的 `_THROTTLED_MESSAGE` 搬到 `base.py` 共用）。

**check**：`GET /config`，成功返回「已连接」。

**list(dir)**：`GET /file/sort`，query `pdir_fid=<dir.id>&_size=100&_page=<从1起>&_fetch_total=1&fetch_all_file=1&fetch_risk_file_name=1`。
- 条目在 `data.list[]`：`fid` → id，`file_name`（`html.unescape`）→ name，`file`（true 为文件）→ `is_dir = not file`，`size`，`updated_at` / `created_at`（毫秒时间戳）。
- `_page * 100 >= metadata._total` 时停止。

**link(file)**：`POST /file/download`，JSON `{"fids": ["<file.id>"]}`，额外带 `User-Agent: <UA>`。取 `data[0].download_url`，为空则失败。
返回 `Link(url, headers={"Cookie": cookie, "Referer": referer, "User-Agent": UA}, proxy=True, concurrency=3, part_size=10*1024*1024, expires_in=None)`。
（OpenList：`getDownloadLink` 的 `Concurrency: 3, PartSize: 10*utils.MB`；夸克在 `Init` 里强制 `WebProxy = true`，UC 为 `OnlyProxy`——两者都只能转发。）

**凭据来源**：浏览器登录 `pan.quark.cn`（UC 为 `drive.uc.cn`）→ F12 → 网络 → 任意 `clouddrive` 请求 → 复制请求头里完整的 `Cookie`。

不做：扫码登录、`use_transcoding_address`、`only_list_video_file`、排序选项。

---

## A.2 阿里云盘 Open（`connectors/drives/aliyun.py`，参照 `drivers/aliyundrive_open`）

字段：
- `refresh_token`（secret，必填）
- `drive_type`（select：`resource` 资源库 / `default` 备份盘(默认盘) / `backup` 备份盘，默认 `resource`）
- `alipan_type`（select：`default` / `alipanTV`，默认 `default`；高级）
- `client_id`、`client_secret`（secret；高级；提示「留空则通过 OpenList 的令牌服务续期」）
- `renew_api`（高级，默认 `https://api.oplist.org/alicloud/renewapi`）
- `root_id`（高级，默认 `root`）
- 驱动回写（不在表单显示）：`access_token`、`drive_id`、`user_id`。

**刷新令牌**（OpenList `util.go` `_refreshToken`）：最多尝试 3 次。
- 填了 `client_id` 和 `client_secret`：`POST https://openapi.alipan.com/oauth/access_token`，JSON `{"client_id", "client_secret", "grant_type": "refresh_token", "refresh_token"}` → `refresh_token`、`access_token`；失败信息在 `code` / `message`。
- 否则：`GET <renew_api>?refresh_ui=<refresh_token>&server_use=true&driver_txt=<alicloud_qr | alicloud_tv>`（`alipan_type=alipanTV` 时为 `alicloud_tv`）→ `refresh_token`、`access_token`；失败信息在 `text`。
- 两个令牌任一为空即失败；`refresh_token` 被拒 → `AuthError("阿里云盘 refresh_token 已失效，请重新获取")`。成功后两者都写回并 `save()`。

**请求**：base `https://openapi.alipan.com`，header `Authorization: Bearer <access_token>`，JSON body。错误响应 `{code, message}`。
- `access_token` 为空，或 `code` 为 `AccessTokenInvalid` / `AccessTokenExpired` / `I400JD` → 刷新后重试**一次**。
- HTTP 429 → 限流（`throttle` 默认已处理）。

**初始化**（首次 list / link / check 前，结果缓存在实例上）：`POST /adrive/v1.0/user/getDriveInfo` → `<drive_type>_drive_id`（如 `resource_drive_id`；为空时报「该账号没有<资源库/备份盘>」）、`user_id`、`name`。

**check**：执行初始化，返回「已连接：<name>」。

**list(dir)**：`POST /adrive/v1.0/openFile/list`，JSON `{"drive_id", "parent_file_id": dir.id, "limit": 200, "marker": <上一页 next_marker，首页 "">}`，直到 `next_marker == ""`。
条目 `items[]`：`file_id`、`name`、`size`、`type`（`folder` 为目录）、`created_at`、`updated_at`（ISO 字符串）。

**link(file)**：`POST /adrive/v1.0/openFile/getDownloadUrl`，JSON `{"drive_id", "file_id": file.id, "expire_sec": 14400}` → `url`，为空则失败。
返回 `Link(url, proxy=False, expires_in=60)`（OpenList `Expiration: time.Minute`；默认 `web_proxy=false`，走 302）。

**凭据来源**：打开 `https://api.oplist.org/`（OpenList Token 获取工具）→ 阿里云盘 Open（或阿里云盘 TV）→ 扫码 → 复制 refresh_token。界面写明该服务由 OpenList 团队提供。

不做：上传、删除方式、LIVP、`callback_concurrency` 代理读取、排序选项。

---

## A.3 百度网盘（`connectors/drives/baidu.py`，参照 `drivers/baidu_netdisk`）

**路径寻址**：`Entry.id` = 网盘绝对路径（如 `/我的资源/电影`），`extra["fs_id"]` = 数字 fs_id。`root_id` 取字段 `root_path`（默认 `/`）。

字段：`refresh_token`（secret，必填）、`client_id` / `client_secret`（secret，高级）、`renew_api`（高级，默认 `https://api.oplist.org/baiduyun/renewapi`）、`root_path`（默认 `/`）。回写：`access_token`。

**刷新令牌**：
- 没填 client 凭据：`GET <renew_api>?refresh_ui=<refresh_token>&server_use=true&driver_txt=baiduyun_go` → `refresh_token`、`access_token`；失败信息在 `text`。
- 填了：`GET https://openapi.baidu.com/oauth/2.0/token?grant_type=refresh_token&refresh_token=…&client_id=…&client_secret=…`；失败信息在 `error` / `error_description`。
- 任一令牌为空即失败；成功写回并 `save()`。

**请求**：base `https://pan.baidu.com/rest/2.0`，所有请求 query 带 `access_token`。响应 `errno != 0` 即失败。
- `errno` 为 `111`、`-6`、`20016`、`20017`，或 `access_token` 为空 → 刷新后重试一次；刷新失败 → `AuthError`。
- `errno == 31034`（命中频控，**未经实测**，在 `progress.md` 记录真实值）→ 限流。

**check**：`GET /xpan/nas?method=uinfo` → 返回「已连接：<baidu_name>（会员类型 <vip_type>）」。

**list(dir)**：`GET /xpan/file?method=list&dir=<dir.id>&web=web&order=name&start=<偏移>&limit=1000`，`start` 每页加 1000，直到 `list` 为空。
条目：`server_filename` → name，`path` → id，`fs_id` → `extra["fs_id"]`，`size`，`isdir`（1 为目录），`server_mtime` / `server_ctime`（秒）。

**link(file)**（官方接口，OpenList `linkOfficial`）：
1. `GET /xpan/multimedia?method=filemetas&fsids=[<fs_id>]&dlink=1` → `list[0].dlink`。`resolve` 拿到的 Entry 一定带 `fs_id`。
2. 对 `<dlink>&access_token=<token>` 发 `HEAD`，header `User-Agent: pan.baidu.com`，`allow_redirects=False`，取响应头 `Location` 为最终地址（为空则沿用该 URL）。
3. 返回 `Link(url, headers={"User-Agent": "pan.baidu.com"}, proxy=True, concurrency=0, part_size=0, expires_in=3600)`——单连接转发（浏览器无法自带该 UA；OpenList 也默认 `web_proxy=true`）。

**凭据来源**：`https://api.oplist.org/` → 百度网盘 → 授权 → 复制 refresh_token。

不做：`crack` / `crack_video`、上传、排序选项、`only_list_video_file`。

---

## A.4 123 云盘 Open（`connectors/drives/pan123.py`，参照 `drivers/123_open`）

字段（三选一，表单说明「任选一种」）：
- `refresh_token`（secret）：走续期服务 `renew_api`（高级，默认 `https://api.oplist.org/123cloud/renewapi`）。
- `client_id` + `client_secret`（secret）：开发者自有凭据。
- `access_token`（secret）：直接填令牌（约 90 天有效，到期需手动更新）。
- `root_id`（高级，默认 `0`）。回写：`access_token`、`expires_at`（ISO 8601）。

三者都没填 → 保存时报「请填写 refresh_token、client_id/client_secret 或 access_token 之一」。

**取令牌**（`access_token` 为空，或 `expires_at` 距现在不足 5 分钟时）：
- 有 `refresh_token`：`GET <renew_api>?refresh_ui=<refresh_token>&server_use=true&driver_txt=123cloud_oa` → `access_token`、`refresh_token`、`expires_in`（秒）；错误信息依次取 `error_description`、`text`、`message`、`error`。新 refresh_token 写回。
- 否则有 client 凭据：`POST https://open-api.123pan.com/api/v1/access_token`，header `Platform: open_platform`，JSON `{"clientID", "clientSecret"}` → `code == 0` 时 `data.accessToken`、`data.expiredAt`（RFC 3339）。
- 只有 `access_token`：直接用；收到 401 → `AuthError("123 云盘 access_token 已失效，请更新")`。
- 成功写回 `access_token`、`expires_at` 并 `save()`。

**请求**：base `https://open-api.123pan.com`，headers `Authorization: Bearer <token>`、`Platform: open_platform`、`Content-Type: application/json`。响应 `{code, message, data}`：
- `code == 0` 成功；`code == 401` → 强制刷新后重试一次；`code == 429` → 限流（OpenList 是 sleep 0.5 s 后重试，这里交给 throttle）；其余失败。

**check**：`GET /api/v1/user/info` → 「已连接：<data.nickname>」。

**list(dir)**：`GET /api/v2/file/list?parentFileId=<dir.id>&limit=100&lastFileId=<首页 0>&trashed=false&searchMode=&searchData=`，用返回的 `data.lastFileId` 继续，直到它为 `-1`。
条目 `data.fileList[]`：`fileId`、`filename`、`size`、`type`（1 为目录）、`trashed`（非 0 的跳过）、`createAt` / `updateAt`（`YYYY-MM-DD HH:MM:SS`，北京时间 UTC+8）。

**link(file)**：`GET /api/v1/file/download_info?fileId=<file.id>` → `data.downloadUrl`，为空则失败。
返回 `Link(url, proxy=True, concurrency=0, part_size=0, expires_in=None)`——单连接转发（OpenList 为该驱动设了 `PreferProxy`，默认 `web_proxy=true`）。

**凭据来源**：`https://api.oplist.org/` → 123 云盘 → 授权 → 复制 refresh_token；或 123 开放平台申请 client_id / client_secret。

不做：直链（DirectLink）模式、上传。

---

## A.5 （保留编号，暂无）

---

## A.6 本机目录（`connectors/drives/local.py`）

字段：`root`（kind `path`，必填，绝对路径）。`check()`：目录存在且可读 → 「已连接：<root>」，否则 `DriveError("目录不存在或无法读取")`。

- `Entry.id` = 相对 `root` 的 POSIX 路径；根为 `""`。
- `list(dir)`：`os.scandir`；跳过以 `.` 开头的项与无法 `stat` 的项；`modified` / `created` 取 `st_mtime` / `st_ctime`（本地时区转带时区 ISO）。
- 路径安全：每次把 id 拼回 `root` 后 `resolve()`，结果必须仍在 `root.resolve()` 之内（防 `..` 与符号链接逃逸），否则 `DriveError("路径超出挂载目录")`。
- `link(file)`：`Link(local_path=<绝对路径>)`。
- 用 `services/paths.py` 处理路径；Windows 上盘符与反斜杠由 `pathlib` 处理，id 一律存 `/` 分隔。

## A.7 AList / OpenList 透传（`connectors/drives/alist.py`）

由现有 `connectors/alist.py` 改写，`kind = "alist"`，固定 `mount = '*'`（每个数据目录最多一个），路径寻址：`Entry.id` = AList 完整路径（含第一段挂载名）。

字段：`origin`（默认 `http://127.0.0.1:5244`）、`username`、`password`（secret）、`path_password`（secret，高级）。回写：无（token 只缓存在内存）。

- 登录：`POST /api/auth/login` `{"username", "password"}` → `data.token`，内存缓存 1 小时；接口返回 401 时清缓存、重新登录并重试一次。
- `list(dir)`：`POST /api/fs/list` `{"path": dir.id, "password": path_password, "page": 1, "per_page": -1, "refresh": false}` → `data.content[]`（`name`、`is_dir`、`size`、`modified`、`created`）。
- `link(file)`：`POST /api/fs/get` `{"path", "password"}` → `data.sign`；`url = origin + "/d" + quote(path, safe="/") + ("?sign=" + sign if sign else "")`（AList 关闭签名时 `sign` 为空，不再像旧代码那样报错）；`Link(url, proxy=False, expires_in=None)`（302 到 AList，由 AList 再按自身设置跳转或代理）。
- 限流识别沿用 `_THROTTLED_MESSAGE`。
- `check()`：登录成功并 `list` 根目录 → 「已连接：<origin>」。

---

## 与 OpenList 行为对照（实现时逐条核对）

| 行为 | OpenList 出处 | MovieDB 对应 |
|---|---|---|
| 目录缓存 30 分钟 | `internal/op/driver.go`：存储项 `cache_expiration` 默认 30 | `media_source` 目录缓存 TTL 1800 s |
| 链接缓存 = 驱动给的 `Expiration`；没有时不按时间过期 | `internal/op/fs.go` `Link`、`internal/op/link_lifecycle.go` | `open_link`：`expires_in` 有值按它；`None` 时最长 1800 s，或上游 401/403/410 时作废 |
| 链接缓存整体上限 30 分钟 | `internal/op/cache.go` linkCache | 同上（1800 s 封顶） |
| 并发分段只在 `Concurrency > 0 || PartSize > 0` 时启用 | `internal/stream/util.go`、`server/common/proxy.go` | `media_stream` 两种转发方式的分支条件 |
| 并发 / 分段为 0 时的默认值 2 路 / 8 MiB | `internal/net/request.go` `DefaultDownloadConcurrency`、`DefaultDownloadPartSize` | 同 |
| 夸克 3 路 × 10 MiB、必须代理 | `drivers/quark_uc/util.go` `getDownloadLink`；`driver.go` `Init` | A.1 |
| 阿里 1 分钟有效、302 | `drivers/aliyundrive_open/driver.go` `Link` | A.2 |
| 百度 1 小时有效、带 UA、默认代理 | `drivers/baidu_netdisk/util.go` `linkOfficial`；`meta.go` `PreferProxy` | A.3 |
| 123 无有效期、默认代理 | `drivers/123_open/driver.go` `Link`；`meta.go` `PreferProxy` | A.4 |
| `/d`（302 或代理）与 `/p`（强制代理） | `server/handles/down.go` | 统一为 `/stream`，按 `Link.proxy` 决定 |

---

## 附录 B · 测试清单

全部用假 Session / 临时目录，不访问网络。文件名为建议值。

**base / store（P2）** — `tests/test_drive_store.py`
- `save_drive` / `get_drive` / `list_drives` 往返；`update_config` 只改 config；`rename_mount` 与 `delete_drive`。
- `list_drives()` 等对外返回值中不含 secret 字段原文（页面层用的序列化函数要测）。

**local_store 迁移（P1）** — `tests/test_local_store.py`
- 只有 `discovery.db`（含 `-wal`）→ 升级后为 `local.db`，数据还在，旧文件不存在。
- 同时有 `api-usage.db` → 两张表并入，旧文件删除；重复执行 `upgrade()` 无副作用。
- `data_reset`：「识别缓存」清空除 `drive` 外的表；「连接与密钥」清空 `drive` 表与 `.env`。

**media_source（P2）** — `tests/test_media_source.py`（用假驱动）
- `split` 拒绝空段、`.`、`..`。
- 挂载规则：命中挂载；未命中且有 `*` → 完整路径交给透传驱动；都没有 → 「找不到挂载」。
- `"/"` 列出挂载并合并透传根目录，重名以挂载为准。
- 目录缓存命中、`refresh=True` 绕过、30 分钟过期（注入时钟）、`forget()`。
- 链接缓存：`expires_in=60` 到期重取；`None` 最长 30 分钟；`fresh=True` 作废；并发取同一路径只调用一次 `link()`。
- `stream_url` 验签通过；改一个字节验签失败；`stream.key` 首次生成后复用。
- `parse_source` 接受 `/挂载/路径` 与旧 `http://host/d/%E7%94%B5%E5%BD%B1/a.mkv?sign=x`。

**media_stream（P2）** — `tests/test_media_stream.py`（Flask test client + 假上游）
- 验签失败 403；本机文件 200 / 206（Range）；`proxy=False` → 302。
- 单连接转发：透传 Range，原样返回 206 与 `Content-Range`；上游 403 → 重取链接重试一次。
- 并发分段：`size=25 MiB`、`concurrency=3`、`part_size=10 MiB` 时上游收到 3 个正确的 Range，输出顺序与内容正确；带 `Range: bytes=5-` 时只请求所需段并返回 206；某段失败重试一次；`concurrency=0, part_size=0` 不走分段。
- `HEAD` 只返回头、`Content-Length` 正确。

**local 驱动（P2）** — `tests/test_drive_local.py`
- 列目录、跳过隐藏文件；`..` 与指向 root 外的符号链接被拒；`link` 返回 `local_path`。

**alist 透传（P3）** — `tests/test_drive_alist.py`
- 登录缓存、401 重登重试、`sign` 拼接、限流消息识别；`.env` 自动迁移只建一次。

**各网盘（P5–P8）** — `tests/test_drive_<kind>.py`，每个都覆盖：
- `check` 成功 / 凭据失效抛 `AuthError`。
- 分页：构造 ≥2 页数据，`list` 返回全部且字段映射正确（时间带时区）。
- `link` 返回的 `proxy` / `headers` / `concurrency` / `part_size` / `expires_in` 与本文件一致。
- 令牌刷新：触发条件命中 → 刷新 → 重试成功 → `save()` 被调用且新令牌已写入；刷新失败 → `AuthError`。
- 限流响应被识别（`throttled` 返回 True）。
- 异常信息与日志中不含 cookie / token。
- 专项：夸克 `__puus` 回写、`html.unescape`；阿里 `drive_type` 取对应 drive_id；百度 `HEAD` 不跟随跳转且取 `Location`；123 三种凭据方式、`expires_at` 不足 5 分钟提前刷新、`trashed` 过滤。

---

## 附录 C · OpenList 源码对照

| MovieDB | OpenList（v4.2.9） | 只看这些函数 |
|---|---|---|
| `drives/quark.py` | `drivers/quark_uc/{meta,driver,util,types}.go` | `Init`、`List`、`Link`、`request`、`GetFiles`、`getDownloadLink`（不看 `getTranscodingLink`、`up*`） |
| `drives/aliyun.py` | `drivers/aliyundrive_open/{meta,driver,util,limiter,types}.go` | `Init`、`List`、`Link`、`refreshToken`、`_refreshToken`、`requestReturnErrResp`、`getFiles` |
| `drives/baidu.py` | `drivers/baidu_netdisk/{meta,driver,util,types}.go` | `Init`、`List`、`refreshToken`、`_refreshToken`、`request`、`getFiles`、`linkOfficial` |
| `drives/pan123.py` | `drivers/123_open/{meta,driver,token,util,types}.go` | `Init`、`List`、`Link`、`Request`、`getAccessToken`、`flushAccessToken`、`getFiles`、`getDownloadInfo` |
| `drives/alist.py` | 现有 `connectors/alist.py`（不参照 OpenList 的 `drivers/alist_v3`） | — |
| `media_source.py` 缓存 | `internal/op/fs.go`、`internal/op/cache.go`、`internal/op/link_lifecycle.go` | `List`、`Link` |
| `media_stream.py` | `server/common/proxy.go`、`internal/stream/util.go`、`internal/net/request.go`、`server/handles/down.go` | `Proxy`、`GetRangeReaderFromLink`、`Downloader.Download` |

其余文件（上传、离线下载、WebDAV、S3、FTP、搜索）与本计划无关，不要参照。
