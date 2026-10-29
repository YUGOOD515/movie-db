# 用 Python 网盘层替换 AList

> 定稿日期：2026-10-29。依据 MovieDB `main@40ff07e` 与 OpenList `v4.2.9`（`fb692b5`）源码。
> 网盘请求规格与 OpenList 对照见同目录 `findings.md`；每完成一个阶段在 `progress.md` 记录。

## 目标

- 去掉对 AList 程序的依赖：MovieDB 内置**只读、精简**的 Python 网盘层，只做两件事——**列目录**、**按文件取可播放链接**。
- 支持：**夸克 / UC、阿里云盘 Open、百度网盘、123 云盘 Open、本机目录**；另保留「连接已有 AList / OpenList」兼容驱动，保证老用户升级后无感。
- 驱动逻辑参照 OpenList 对应 Go 驱动改写；项目许可证改为 **AGPL-3.0**。
- 播放、拖动进度、读取媒体信息的**最终效果与 OpenList 一致**（同样的转发方式、并发分段、链接缓存与目录缓存时长）。
- 已有媒体库**不迁移数据**：`media_file.alist_path` 继续保存 `/挂载名/相对路径`。
- 最终体验：首次引导直接「添加网盘」→ 扫描 → 关联 → 播放，不需要安装或配置 AList。

## 不做（范围外，不得加入）

- 任何写操作：上传、新建文件夹、移动、改名、删除、转存、分享。
- 离线下载、转码、WebDAV / S3 / FTP、多用户、搜索索引、缩略图。
- 115、PikPak、天翼等其他网盘；夸克扫码登录与转码地址（`use_transcoding_address`）；百度 `crack` / `crack_video`；123 直链（DirectLink）模式。
- 新的第三方依赖：只用已有的 `requests` 与标准库。
- 重命名数据库列 `alist_path`（另立计划）。

## 已定决策

| # | 问题 | 结论 |
|---|---|---|
| 1 | 网盘配置存哪 | 不新建数据库。`discovery.db` 改名为 **`local.db`（本机库）**，形成两层：`library.db` 只放页面展示用的媒体库数据（参与云端同步）；`local.db` 放其余全部本机状态（扫描与识别缓存、关联任务、网盘配置与凭据、API 用量与评分缓存）。`api-usage.db` 一并并入 `local.db`。 |
| 2 | 令牌续期是否默认走 `api.oplist.org` | 默认走（与 OpenList 一致）。界面写明这是 OpenList 团队提供的第三方服务；高级设置可改填自己的 client_id / client_secret。 |
| 3 | 是否保留 AList / OpenList 兼容驱动 | 保留，作为「透传挂载」，老用户升级后无需任何操作即可继续使用。 |
| 4 | 夸克转发方式 | 与 OpenList 一致：必须经 MovieDB 转发，**3 路并发、每段 10 MiB**。 |
| 5 | 链接与目录缓存时长 | 与 OpenList 一致：目录缓存 30 分钟；链接按驱动给出的有效期缓存（阿里 1 分钟、百度 1 小时），驱动未给有效期的（夸克、123）沿用到上游失效为止，最长 30 分钟。 |

## 给实现 Agent 的约定

- 先读 `AGENTS.MD`，遵守分层：路由只编排；读写数据库只在 `models/`；对外请求只在 `connectors/`；业务编排在 `services/`。
- 按阶段顺序做，每个阶段一个 PR；**上一阶段测试全绿再开始下一阶段**，并在 `progress.md` 记录完成情况与偏差。
- 每个驱动文件控制在约 200 行以内。只实现本计划列出的接口与字段，不要「顺手」加功能；遇到计划没覆盖的情况，先在 `progress.md` 记下并停下来问。
- 每个驱动只用一个 `requests.Session`，构造时可注入（测试传入假 Session）；所有网盘 API 请求（列目录、取链、刷新令牌）都经 `connectors/throttle.py` 的 `throttle.call(...)`；`/stream` 转发文件数据的请求除外（见 `findings.md`「通用规则」）。
- 凭据（Cookie、token、secret、密码）**不得**出现在日志、异常信息、页面或 JSON 响应里；面向用户的报错用中文，不带 URL 与凭据。
- 凭据发生变化（夸克 `__puus`、各家新 refresh_token / access_token）时**立即**通过 `save()` 回调持久化——丢掉新 refresh_token 就等于登录失效。
- 路径一律走 `services/paths.py`；代码需同时在 Windows 与 macOS 上运行。
- 新增或删除环境变量时同步 `services/settings.py` 的 `SECTIONS` 与 `.env.example`。
- 测试不访问真实网络、不启动真实进程。

## 结构

```
models/local_store.py        # 原 discovery_store.py 改名：local.db 的路径、连接、建表、旧库迁移
models/import_store.py       # 不变，改为引用 local_store
models/api_usage.py          # 改为写入 local.db
models/drive_store.py        # local.db 中 drive 表的读写
connectors/drives/
  __init__.py                # DRIVERS 注册表：kind → 驱动类
  base.py                    # Entry / Link / Field / Drive 基类 / 异常
  local.py                   # 本机目录
  alist.py                   # 连接已有 AList / OpenList（透传挂载）
  quark.py                   # 夸克 + UC（同一个类，两组常量）
  aliyun.py                  # 阿里云盘 Open
  baidu.py                   # 百度网盘
  pan123.py                  # 123 云盘 Open
services/media_source.py     # 门面：挂载解析、路径→对象、目录缓存、链接缓存、stream_url
services/media_stream.py     # /stream 的业务：302 跳转 / 带头转发（含并发分段）/ 本机文件
templates/drives.html        # 网盘页
templates/_drive_form.html   # 添加 / 编辑表单片段（网盘页与首次引导共用）
static/drives.js             # 网盘页脚本
```

数据流：`services/*` → `services/media_source.py` → `connectors/drives/<kind>.py` → 网盘接口。播放器与 ffprobe 只拿到 MovieDB 自己的 `/stream/...` 地址。

## 接口定义（`connectors/drives/base.py`）

```python
@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    id: str                 # 驱动自定义：ID 寻址的网盘填文件 ID；百度、本机、AList 填路径
    size: int = 0
    modified: str = ""      # ISO 8601，带时区
    created: str = ""
    extra: dict = field(default_factory=dict, compare=False)  # 如百度 fs_id

@dataclass(frozen=True)
class Link:
    url: str = ""
    headers: dict = field(default_factory=dict)
    expires_in: int | None = None  # 秒；None 表示驱动未给有效期（见决策 5）
    proxy: bool = False            # True：必须由 MovieDB 带 headers 转发；False：302 跳转
    concurrency: int = 0           # 转发时的并发连接数；0 表示单连接
    part_size: int = 0             # 并发分段大小（字节）
    local_path: str = ""           # 仅本机驱动使用

@dataclass(frozen=True)
class Field:
    name: str               # 配置键，如 "cookie"
    label: str              # 表单标签（中文）
    kind: str = "text"      # text / secret / select / bool / path
    required: bool = False
    default: str = ""
    options: tuple = ()     # select 用：((值, 显示名), ...)
    hint: str = ""
    advanced: bool = False

class DriveError(RuntimeError): ...   # 通用失败，message 可直接展示
class AuthError(DriveError): ...      # 凭据失效，需要用户重新填写

class Drive:
    kind: ClassVar[str]               # "quark"
    label: ClassVar[str]              # "夸克网盘"
    fields: ClassVar[tuple[Field, ...]]
    root_id: ClassVar[str]            # 根目录 ID；路径寻址的驱动可在 __init__ 里按配置覆盖

    def __init__(self, config: dict, save: Callable[[dict], None], session=None): ...
    def root(self) -> Entry: ...      # Entry(name="", is_dir=True, id=self.root_id)
    def check(self) -> str: ...       # 连接检测；返回给用户看的一句话，如「已连接：昵称」
    def list(self, directory: Entry) -> list[Entry]: ...  # 必须翻完全部分页
    def link(self, file: Entry) -> Link: ...
```

## 阶段

### P0 · 许可证

1. 根目录加 `LICENSE`（AGPL-3.0 全文，取自 gnu.org）；`pyproject.toml` 加 `license = "AGPL-3.0-or-later"`。
2. 加 `NOTICE`：说明 `connectors/drives/` 中的驱动参照 OpenList（AGPL-3.0，https://github.com/OpenListTeam/OpenList ）对应驱动改写。
3. README 中英文各加一行许可证说明。

完成标准：只有文档与许可证文件变化，测试全绿。

### P1 · 存储分层：`discovery.db` → `local.db`

1. `models/discovery_store.py` 改名为 `models/local_store.py`，文件名 `discovery.db` 改为 `local.db`；模块文档写明两层分工（见决策 1）。所有 `from models import discovery_store` 改为 `local_store`。
2. 环境变量 `MOVIEDB_DISCOVERY_DATABASE` 改名为 `MOVIEDB_LOCAL_DATABASE`，读取时兼容旧名；`MOVIEDB_API_USAGE_DATABASE` 删除。同步 `services/settings.py`、`tests/conftest.py`、`tests/test_data_reset.py`。
3. `models/api_usage.py` 的两张表（`request_usage`、`api_response`）改建在 `local.db`。
4. 旧库迁移 `local_store.upgrade()`，只在本机启动时调用（只读云端不调用），可重复执行：
   - `local.db` 不存在而 `discovery.db` 存在：在打开任何连接之前，把 `discovery.db` 及其 `-wal` / `-shm` 一起改名为 `local.db*`。
   - `api-usage.db` 存在：`ATTACH` 后 `INSERT OR IGNORE` 两张表的数据，`DETACH`，删除 `api-usage.db*`。
5. `services/data_reset.py`：`local.db` 改为**按表清空**，不再删整个文件：
   - 「识别缓存与扫描记录」：清空 `local.db` 中除 `drive` 外的全部表（含关联任务、API 用量与评分缓存），等同于现在的行为。
   - 「连接与密钥」：清除 `.env`，并清空 `drive` 表；提示文字改为「网盘、TMDB、AI 等全部设置与凭据」。
6. 检查 `services/cloud_sync.py`、`scripts/publish_snapshot.py`：确认只同步 `library.db`。

完成标准：全部测试全绿；用旧数据目录启动后，原有扫描记录、关联任务、评分缓存都还在，`discovery.db`、`api-usage.db` 不再存在。

### P2 · 核心层 + 本机驱动 + 播放转发

1. `connectors/drives/base.py`：按上面的接口定义实现。
2. `connectors/drives/local.py`（规格见 `findings.md` A.6）。
3. `models/drive_store.py`，表建在 `local.db`：

```sql
CREATE TABLE IF NOT EXISTS drive (
    mount       TEXT    NOT NULL PRIMARY KEY,  -- 挂载名，如「夸克」；不含 /。AList 透传驱动固定为 '*'
    kind        TEXT    NOT NULL,
    config      TEXT    NOT NULL,              -- JSON，含凭据与驱动回写的令牌
    enabled     INTEGER NOT NULL DEFAULT 1,
    status      TEXT    NOT NULL DEFAULT '',   -- 最近一次检测结果（不含凭据）
    sort_order  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL
);
```

   函数：`list_drives()`、`get_drive(mount)`、`save_drive(mount, kind, config, enabled)`、`update_config(mount, config)`（供 `save()` 回调，只改 config）、`set_status(mount, status)`、`delete_drive(mount)`、`rename_mount(old, new)`。写操作加进程内锁。

4. `services/media_source.py`（门面）：
   - `VIDEO_EXTENSIONS`：从 `connectors/alist.py` 搬过来。
   - `drive_for(mount)`：返回驱动实例（进程内缓存，配置保存或删除时作废）；`save` 回调 = `drive_store.update_config(mount, …)`。
   - `split(path) -> (mount, parts)`：`"/夸克/电影/a.mkv"` → `("夸克", ["电影", "a.mkv"])`；拒绝空段、`.`、`..`。
   - **挂载规则**：第一段等于某个已启用挂载名 → 交给该驱动，传入其余部分；否则若存在已启用的 AList 透传驱动（`mount='*'`）→ 把**完整路径**交给它；否则报「找不到挂载：<名称>」。
   - `list_directory(path, *, refresh=False) -> list[dict]`：返回与旧 AList 相同形状的字典 `{"name", "is_dir", "size", "modified", "created"}`。`"/"` 返回所有已启用挂载，再并上 AList 透传驱动根目录的子项（重名时以挂载为准）。
   - `resolve(path, *, refresh=False) -> (drive, Entry)`：从 `drive.root()` 逐级 `list`，按名字**精确匹配**；找不到抛 `FileNotFoundError("找不到：<path>")`。
   - 目录缓存：键 `(mount, 目录路径)`，TTL 30 分钟（OpenList `cache_expiration` 默认值）；`refresh=True` 时绕过并覆盖；`forget(mount=None)` 清空。
   - `get_file(path) -> {"signed_url", "raw_url", "size", "modified"}`：先用缓存解析，找不到时用 `refresh=True` 再解析一次；`signed_url` 与 `raw_url` 都填 `stream_url(path)`；是目录则报错。
   - `open_link(path, *, fresh=False) -> (Link, Entry)`：链接缓存，键为 path。`expires_in` 非空时按它缓存；为 `None` 时最长缓存 30 分钟。上游失效由 `media_stream` 调 `fresh=True` 作废重取。同一路径并发请求只取一次链接（加锁）。
   - `stream_url(path)`：`http://127.0.0.1:{MOVIEDB_PORT，缺省 5000}/stream/{token}/{quote(文件名)}`。`token = base64url(path) + "." + hmac_sha256(key, path) 的前 32 位十六进制`；`key` 存在 `paths.DATA_DIR / "stream.key"`，首次运行用 `secrets.token_bytes(32)` 生成。
   - `parse_source(value) -> path`：接受 `/挂载/路径`，也兼容旧 AList 直链 `http(s)://…/d/<路径>?sign=…`（取 `/d/` 之后的部分并 URL 解码，忽略域名与 sign）。
5. `services/media_stream.py` + `app.py` 路由 `GET|HEAD /stream/<token>/<path:name>`：
   - 验签失败 → 403。
   - `Link.local_path` → `send_file(local_path, conditional=True)`（werkzeug 处理 Range）。
   - `proxy=False` → `redirect(link.url, 302)`。
   - `proxy=True` 且 `concurrency == 0 and part_size == 0` → **单连接转发**：用驱动的 Session 请求 `link.url`，带上 `link.headers` 和客户端的 `Range` / `If-Range`，`stream=True`，超时 `(10, 60)`。原样返回 200 / 206 状态码，以及 `Content-Type`、`Content-Length`、`Content-Range`、`Accept-Ranges`、`Last-Modified`、`ETag`。正文按 1 MiB 分块用 `stream_with_context` 输出；生成器 `finally` 里关闭上游响应。
   - `proxy=True` 且 `concurrency > 0 or part_size > 0` → **并发分段转发**（对应 OpenList `internal/net` 的 Downloader）：
     - 并发数取 `concurrency`（为 0 时取 2），分段取 `part_size`（为 0 时取 8 MiB）——与 OpenList 默认值一致。
     - 按客户端请求的字节区间（无 Range 时为整个文件，文件大小取自 `Entry.size`）切成若干段，每段单独发 `Range: bytes=a-b`；最多同时进行 `concurrency` 段，**按顺序**输出，内存上限约 `concurrency × part_size`。
     - 响应头自行构造：有 Range 时 206 + `Content-Range: bytes start-end/size`，否则 200；`Content-Length`、`Accept-Ranges: bytes`、`Content-Type` 按扩展名猜测。
     - 某段失败：作废链接重取后重试该段一次，仍失败则结束响应。客户端断开时取消未完成的段。
   - 上游返回 401 / 403 / 410 → `open_link(path, fresh=True)` 重试一次。
   - `HEAD`：本机文件直接给出大小；其他情况向上游发 `GET` 并带 `Range: bytes=0-0`，从 `Content-Range` 取总大小，只返回响应头。
   - 该路由**不**加入 `READ_ONLY_ENDPOINTS`。
6. `connectors/throttle.py`：新增 `drive` 策略（`DRIVE_RATE` 默认 20、`DRIVE_CONCURRENCY` 默认 4，取值范围沿用旧 `ALIST_*`），以及 `findings.md` 中各网盘的专用策略；补全 `LABELS`。

完成标准：`findings.md` 附录 B 中 base / store / stream / local 的测试全绿；旧功能尚未切换，原有测试不受影响。

### P3 · 切换调用点 + AList 透传驱动

1. `connectors/drives/alist.py`（规格见 `findings.md` A.7）：由现有 `connectors/alist.py` 的登录、列目录、取链部分改写。
2. 升级自动迁移（放在 `local_store.upgrade()` 之后执行）：`.env` 中有 `ALIST_USERNAME` 与 `ALIST_PASSWORD`，且 `drive` 表里还没有 AList 透传驱动时，用 `ALIST_ORIGIN` / `ALIST_USERNAME` / `ALIST_PASSWORD` / `ALIST_PATH_PASSWORD` 自动创建一个（`mount='*'`）。`.env` 原值不动。
3. 把所有 `from connectors import alist` 改为 `services/media_source.py`：

| 位置 | 改法 |
|---|---|
| `services/media_discovery.py`（`find_share_root`、`collect_folder`、扫描、浏览） | `alist.list_directory(p, token, …)` → `media_source.list_directory(p, refresh=…)`，去掉 token 与 path_password 参数；`alist.VIDEO_EXTENSIONS` → `media_source.VIDEO_EXTENSIONS` |
| `services/media_health.py` | 同上 |
| `services/media_association.py` | 删除 `configured_token()`；占位 `alist_url` 改为直接传 path（同步调整 `import_media_batch` 的输入键）；`alist.get_file(path, token)` → `media_source.get_file(path)` |
| `services/media_import.py` | `parse_alist_url` → `media_source.parse_source`；`validate_alist_video_url` + `get_video_link` → `media_source.get_file(path)`，再校验扩展名在 `VIDEO_EXTENSIONS` 中；批量导入模板 `static/templates/media_import_template.json` 增加 `path` 字段，并兼容旧的 `alist_url` |
| `app.py` 播放路由 | `alist.get_video_link(...)` → `media_source.stream_url(source["alist_path"])` |
| `app.py` `/manager/discovery/browse` | 去掉 `alist.configured_token()` |
| `services/data_reset.py`、`services/settings.py`、`services/setup_wizard.py` 中的 `forget_token` | 改为 `media_source.forget()` |

4. 删除 `connectors/alist.py`、`services/alist_process.py`、`tests/test_alist_process.py`，以及 `app.py` 中 `start_configured_alist` 的调用。
5. `ALIST_IMPORT_ROOT` 改名为 `MEDIA_IMPORT_ROOT`（读取时兼容旧名）；删除其余 `ALIST_*` 配置项与 `.env.example` 中的对应行。
6. 更新受影响的测试：`conftest.py`、`test_library_app.py`、`test_media_association.py`、`test_import_pipeline.py`、`test_smart_import.py`、`test_settings.py`、`test_setup.py`、`test_throttle.py`，把对 `alist.*` 的 monkeypatch 改为 `media_source.*`。
7. 检查 `cloud/renderer/worker.py`、`scripts/publish_snapshot.py`：只读快照不播放，确认只用到 `alist_path` 列名。

完成标准：全部测试全绿。手动验证两种情况：① 本机驱动挂一个目录，扫描 → 关联 → ffprobe 读到参数 → 播放器播放并能拖动进度；② 用旧数据目录（原 AList 配置）启动，已有影片无需任何操作即可播放。全仓搜索 `alist|AList|ALIST`，只剩 `archive/`、CHANGELOG 历史、`alist_path` 列名、AList 透传驱动及其迁移代码。

### P4 · 网盘页与首次引导

1. 页面 `/manager/drives`（侧栏「媒体资源管理」下新增「网盘」）：
   - 列表：每个挂载一张卡片，显示网盘类型、挂载名、状态徽章；按钮为检测 / 编辑 / 停用 / 删除。AList 透传驱动显示为「AList / OpenList（透传）」。
   - 添加 / 编辑：先选类型，再按该驱动的 `fields` 渲染表单（`_drive_form.html`，复用 `templates/_ui.html` 的宏）。`secret` 字段不回显，留空表示保持不变；`advanced` 字段收进「高级」。用到 `api.oplist.org` 的驱动在表单里写明其为 OpenList 团队的第三方服务，并给出获取令牌的链接。
   - 保存前先 `check()`：成功则保存并写 `status`；失败则不保存，显示中文错误。
   - 迁移提示：查询 `media_file.alist_path` 中出现、但既没有对应挂载也没有透传驱动兜底的挂载名（取路径第一段，去重），提示「以下挂载名尚未添加网盘：…，添加时使用相同名称即可继续播放」。
2. 接口：`POST /manager/drives/save`、`POST /manager/drives/<mount>/test`、`POST /manager/drives/<mount>/toggle`、`POST /manager/drives/<mount>/delete`。返回 JSON，不含任何凭据。
3. 首次引导 `/setup`：「片源 · AList」一步改为「添加网盘」，复用同一表单片段；至少添加一个网盘才能进入下一步；删掉 AList 运行状态检测。
4. 设置页：「连接 → AList」组改为「网盘」组，只保留指向网盘页的入口与 `DRIVE_RATE` / `DRIVE_CONCURRENCY`（高级）。
5. 前端遵守 `AGENTS.MD`：Bootstrap 原生组件、Lucide 本地图标；脚本放 `static/drives.js`；不引入 CDN。

完成标准：在全新数据目录下走完首次引导（添加本机目录）后能直接扫描；网盘页增删改查与检测可用；页面与 JSON 响应里搜不到 cookie / token 字样。

### P5 · 夸克 / UC（`findings.md` A.1）
### P6 · 阿里云盘 Open（A.2）
### P7 · 百度网盘（A.3）
### P8 · 123 云盘 Open（A.4）

每个驱动一个 PR，结构相同：驱动文件 + 注册到 `DRIVERS` + 驱动单测（假 Session）+ 网盘页出现该类型。

每个驱动的完成标准：单测覆盖检测、分页列目录、取链、令牌刷新并持久化、限流识别、凭据失效抛 `AuthError`；用真实账号手动验证（由人完成）：添加 → 扫描一个目录 → 播放并拖动进度，与同一账号在 OpenList 下的表现一致。

### P9 · 文档

- README（中、英）：「准备」去掉 AList，改为「添加网盘」；功能列表同步。
- `docs/*/configuration.md`：删除 `ALIST_*`，加入 `DRIVE_RATE`、`DRIVE_CONCURRENCY`、`MEDIA_IMPORT_ROOT`、`MOVIEDB_LOCAL_DATABASE`。
- `docs/*/adding-media.md`：各网盘如何获取凭据（`findings.md` 各节的「凭据来源」）；老用户说明（自动迁移为 AList 透传，也可改用内置网盘、挂载名保持一致）。
- `AGENTS.MD`：Model 段改为两层存储（`library.db` / `local.db`）；Connector 段描述 `connectors/drives/`、`media_source`、`/stream`。
- `CHANGELOG.md`「未发布」写明变化与迁移方法；注明不再代为启动 AList。
