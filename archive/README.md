# Archive

这里保存已经退出运行链路、但仍有排查或数据沿革价值的项目材料。

- `legacy-movie-db/migrations/`：旧 `movie.db` 架构与迁移 SQL，不得用于当前 `library.db`。
- `completed-library-migrations/`：已经执行完毕的一次性 `library.db` 迁移工具。

当前数据库结构以 `migrations/library_schema.sql` 为准；当前运行脚本只放在 `scripts/`。

## 2026-10-14 归档准备记录

以下内容原在根目录 README，文档重写时原样移到这里。文中链接以仓库根目录为基准。

### 使用与归档说明（2026-10-14）

Flask + Jinja 影视库：TMDB 主数据、IMDb / MDBList 评分、AList 媒体来源及 MPC-HC 播放。读查询、写模型、连接器、服务编排和页面 ViewModel 分层保存。

#### 结构与入口

- [app.py](../app.py)：Flask 路由。
- [queries](../queries)：只读查询。
- [models/library.py](../models/library.py)：业务写操作。
- [connectors](../connectors)：外部接口与本地播放器。
- [services](../services)：导入、关联、播放和删除业务。
- [pyproject.toml](../pyproject.toml)：Python 3.13+ 依赖。

#### 使用前提

先阅读原 README 的配置与数据准备，执行 uv sync 后，在自己的本地环境启动 uv run flask --app app run --host 127.0.0.1 --port 5000。需要自己的业务库、TMDB / AList 配置，播放器和 ffprobe 按功能选配。仅克隆源码不等于获得作者的数据库或媒体。

#### 归档分工

**GitHub：** 当前源码、迁移、测试、锁文件、脱敏示例和运行必需图标；优先从当前工作树生成经审核的干净代码快照，避免直接推送含历史凭据 / 数据的旧仓库。

**夸克网盘：** library.db、个人图片、备份与媒体清单只作一致性、加密私人备份。IMDb 评分文件由程序从官方地址自动下载，不需要备份或分享。

#### 当前限制

旧 helpers.py 的历史抽样仍命中凭据模式，library.db 出现在历史；data/white_album_2_import.json 和 static/templates/media_import_template.json 含签名 URL，发布前必须替换为虚构链接。

本次仅完成本地结构、关键入口与依赖检查，以及 README 准备，未验证全部运行功能。本轮未创建 GitHub 仓库或夸克分享链接，也未上传本次归档；后续归档须经目录所有者批准。

作者自有内容尚未统一选择开源许可证；第三方源码和资源沿用原许可，目录内出现的第三方许可证不自动覆盖其他文件。
