# 进度

每完成一个阶段补一条：日期、PR、完成情况、与计划的偏差及原因、遗留问题。

| 阶段 | 状态 | PR | 备注 |
|---|---|---|---|
| P0 许可证 | 已完成 | 已合并 `chore/agpl-license` | 2026-10-29：新增 AGPL-3.0 全文 `LICENSE` 与 OpenList 改写说明 `NOTICE`，`pyproject.toml` 声明 `AGPL-3.0-or-later`，README 中英文许可段改为 AGPL。偏差：保留原许可段改写；`license-files` 未显式声明，PEP 639 默认收录。安装包随附情况待实机验证。 |
| P1 存储分层 | 已完成 | #16 | 2026-10-29：完成 local.db 改名、旧库与 API 缓存迁移、按表清空；阶段完整测试 279 passed、1 skipped、31 subtests passed。偏差：未创建提交或 PR；P9 文档留待后续。 |
| P2 核心层 + 本机驱动 + 播放转发 | 已完成 | #16 | 2026-10-29：完成 base/store、本机驱动、media_source、media_stream 与限流；阶段完整测试 296 passed、1 skipped、31 subtests passed。偏差：未创建提交或 PR。 |
| P3 切换调用点 + AList 透传驱动 | 已完成 | #16 | 2026-10-29：完成透传驱动、旧配置迁移、调用点切换与旧模块删除；最终完整测试 291 passed、1 skipped、31 subtests passed。偏差：P3/P4 在设置与首次引导有交叉，仅做必要后端调整，未提前实现 P4 网盘页和表单，故 UI、文档、CI 与旧测试中仍有计划后续处理的 AList 字样；未创建提交或 PR；真实播放验证待人工。 |
| P4 网盘页与首次引导 | 已完成 | #16 | 2026-10-29：完成网盘 CRUD/检测、共享表单、设置入口与首次引导；完整测试 294 passed、1 skipped、31 subtests passed。未创建提交或 PR；真实界面与播放待人工。 |
| P5 夸克 / UC | 已完成 | #16 | 2026-10-29：完成检测、分页、取链、__puus 回写与限流；阶段完整测试 301 passed、1 skipped、31 subtests passed。未创建提交或 PR；真实账号验证待人工。 |
| P6 阿里云盘 Open | 已完成 | #16 | 2026-10-29：完成初始化、分页、取链、两种续期与令牌回写；阶段完整测试 307 passed、1 skipped、31 subtests passed。未创建提交或 PR；真实账号验证待人工。 |
| P7 百度网盘 | 已完成 | #16 | 2026-10-29：完成路径分页、官方 dlink + HEAD、UA 转发与令牌回写；阶段完整测试 313 passed、1 skipped、31 subtests passed。未创建提交或 PR；errno 31034 仅用构造响应验证，真实频控值仍需账号实测。 |
| P8 123 云盘 Open | 已完成 | #16 | 2026-10-29：完成三种凭据、提前续期、分页与单连接取链；最终完整测试 321 passed、1 skipped、31 subtests passed。未创建提交或 PR；真实账号验证待人工。 |
| P9 文档 | 已完成 | #16 | 2026-10-29：完成中英文 README、配置、添加媒体、开发与远程文档、CHANGELOG 和七组人工测试清单。 |

## 工作记录

- 2026-10-29：用户将范围扩展为连续完成 P1–P3，随后继续 P4–P8。同步更新了 AGENTS.md 的存储与网盘主干说明，未新增依赖。
- 测试命令：`uv run --frozen --with pytest python -m pytest -q`。系统 Python 与项目 `.venv` 起初均无 pytest，改用仓库 README / CI 的标准 uv 命令，未修改项目依赖。
- P1 首轮 1 项失败：旧环境变量兼容测试未清除 autouse 的新变量；修正隔离后通过。P2 首轮 1 项失败：Werkzeug 将 HEAD 空响应长度改为 0；改为建响应后写上游总大小，并补充网络异常重试与 401 非 JSON 重登覆盖。
- P3 首轮 2 项失败：JSON 模板预期未加入 `path`、空白密钥无变化时不创建 `.env`；同步测试预期后通过。一次测试编辑因旧匹配块漏掉 import 行未命中，重读文件后修正。
- P4 首轮 1 项失败：本机挂载还列出测试生成的 local.db，改为断言视频可被发现。P6 首轮 1 项失败：续期失败用例预设了 drive_id，改为未初始化后通过。最终 321 passed、1 skipped、31 subtests passed，git diff --check 无错误。
- 审查修复：新增回归测试修复前 16 failed、1 passed，逐项修复后 17 项全部通过；完整测试 338 passed、1 skipped、31 subtests passed。第 7 项首次引导强制网盘校验原已实现并通过，无行为改动；第 8 项为测试自身的路径转义问题，复现失败后改为检查 JSON 键白名单，Windows/macOS/Linux 通用。旧分段测试补齐真实 206 所需的 Content-Range。
- P0 合并：先提交审查修复与 P9，再合并 `chore/agpl-license`；仅 progress.md 冲突，保留各阶段记录并标记 P0/P9 完成，README 许可段自动合并。
- PR：#16，https://github.com/YUGOOD515/movie-db/pull/16 。合并后验证：`uv lock --check` 通过，338 passed、1 skipped、31 subtests passed。
- 手动验证待办：① 旧数据目录启动后核对扫描记录、关联任务、评分缓存及旧文件删除；② 本机挂载完成扫描 → 关联 → ffprobe → 播放与拖动；③ 原 AList 配置自动迁移后播放；④ 夸克/UC、阿里、百度、123 各自真实账号完成添加 → 扫描 → 播放与拖动，并与 OpenList 对照；⑤ 检查实际播放器、桌面安装版与 macOS 行为。

## 人工测试清单

通用检查：全程打开“管理 → 网盘”和 `/setup/status`，确认页面与 JSON 不出现 Cookie、refresh_token、access_token、user_id、drive_id、expires_at 等凭据或派生值；经 Cloudflare Tunnel 访问网盘页及其读写接口均应返回 403。

### 1. 老用户升级

1. 备份含 `library.db`、`discovery.db`、`api-usage.db` 和 AList 配置的旧数据目录，安装并启动新版。
2. 检查扫描记录、关联任务和评分缓存仍在，`discovery.db*`、`api-usage.db*` 已并入 `local.db`，旧文件不再存在。
3. 打开网盘页，确认已自动创建唯一的 AList / OpenList 透传挂载，`.env` 原值未改；播放一部已有影片并拖动进度。
4. 停用透传后播放应出现中文错误，恢复后应可继续播放；全程不应要求重新关联媒体。

### 2. 本机目录

1. 添加“本机目录”，填写含一部测试影片的绝对路径，使用挂载名 `本机`，检测并保存。
2. 在关联页添加 `/本机` 为扫描目录，确认识别到影片后完成关联；确认隐藏文件未出现，目录外符号链接被拒绝。
3. 打开详情页播放并拖动进度，确认 ffprobe 参数、Range 响应和续播正常；修改目录路径后应重新检测并可继续扫描。
4. 本机无令牌续期环节；删除或移动测试文件后播放应返回中文 404，而不是 500 或堆栈。

### 3. AList / OpenList 透传

1. 自行启动原 AList / OpenList 服务，添加透传账号或沿用升级自动创建的挂载，检测根目录可见。
2. 扫描一个含影片目录并完成关联，播放并拖动进度；分别在有、无 `sign` 的配置下确认 `/d` 链接可播放。
3. 使登录令牌失效，下一次列目录应自动重新登录一次并成功；停止服务时应返回中文 502，恢复后播放成功。
4. 无自动续期令牌可验；确认 MovieDB 不会代为启动或停止 AList 进程。

### 4. 夸克 / UC（两种账号分别执行）

1. 从网页版请求头复制完整 Cookie，添加对应类型和挂载名，检测并保存。
2. 扫描一个有多页文件的目录并完成关联，播放并拖动进度；通过请求日志确认 3 路、10 MiB 分段且顺序正确。
3. 让上游返回新的 `__puus`，确认它立即写入 `local.db`，后续请求使用新值且不回显；使 Cookie 失效，应出现中文重新复制提示而非 500。
4. 令牌恢复后重新播放应成功，与同一账号在 OpenList 中的列目录和拖动表现一致。

### 5. 阿里云盘 Open

1. 用 refresh_token 添加资源库或备份盘，检测时确认确实请求一次 `getDriveInfo`；再次检测仍应重新请求并刷新昵称/盘信息。
2. 扫描含多页文件的目录并完成关联，播放应 302 到上游且拖动正常，链接缓存约 1 分钟。
3. 使 access_token 失效，下一次请求应自动续期并立即保存新 refresh_token / access_token；恢复播放，无需重新添加挂载。
4. 修改 `drive_type` 或 `root_id` 后，旧 access_token、drive_id、user_id 不得复用，应按新配置重新初始化并播放。

### 6. 百度网盘

1. 用 refresh_token 添加挂载并检测昵称/会员信息，扫描一个目录并完成关联。
2. 播放并拖动进度，确认只使用带 `User-Agent: pan.baidu.com` 的单连接转发，链接缓存约 1 小时。
3. 使 access_token 失效，下一次请求应自动续期并保存两个新令牌，恢复播放；构造或观察无 `Location` 的 dlink 时，回退地址必须携带 access_token。
4. 与 OpenList 对照目录和播放表现；页面、异常与日志中不得出现令牌。

### 7. 123 云盘 Open

1. 分别选择 refresh_token、client_id/client_secret 或 access_token 之一添加挂载（至少实测一种可续期方式和一种直接 access_token）。
2. 扫描含多页及回收站文件的目录，确认回收站条目被跳过、游标缺失或重复时不会死循环；完成关联。
3. 播放并拖动进度，确认单连接转发；把 expires_at 调到 5 分钟内，下一次请求应提前续期并保存新令牌和到期时间。
4. 直接 access_token 返回 401 时应提示更新，续期方式恢复后应可继续播放，与 OpenList 表现一致。
