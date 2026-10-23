# 内置 OpenList 后端

## 目标

安装包自带 OpenList（AList 社区分叉）作为媒体来源后端，由 MovieDB 全权管理它的启动、停止、端口、管理员密码与配置；首次引导中选「使用内置 OpenList」即可跳过填写地址和账号，直接进入「添加网盘」。仍保留「连接已有 AList / OpenList」，照顾已有部署的用户。

## 为什么是 OpenList 而不是 AList

- 2025-06 AList 被出售给「不够科技（贵州）」，PR 中出现收集系统信息的代码，社区随即分叉出 OpenList。
- OpenList 同为 AGPL-3.0，接口沿用 AList v3（`/api/auth/login`、`/api/fs/list`、`/api/fs/get`、`/d/...?sign=`），维护活跃，官方提供 lite 版。

## 体积实测（2026-10-23，Windows x64）

| 方案 | exe 本体 | 压缩后 |
|---|---|---|
| AList 官方完整版 v3.66.0 | 113 MB | ≈ 43 MB |
| OpenList 官方 lite v4.2.7 | 92 MB | ≈ 31 MB（zip） |
| 自编译：无前端 + 全部驱动（`CGO_ENABLED=0`，`-s -w`） | 83 MB | ≈ 20 MB（LZMA）/ 28 MB（zip） |
| 自编译：无前端 + 13 个常用驱动 | 71 MB | ≈ 17 MB（LZMA） |

- 体积主要来自核心依赖，裁掉七十多个驱动只省约 3 MB，不值得维护自定义驱动清单。
- lite 前端压缩后仅约 2.8 MB，保留它可直接用 OpenList 自己的「存储」页添加网盘，避免在 MovieDB 里重写各家网盘的登录表单（扫码、refresh token、Cookie 等）。
- 不用 UPX：Windows 上易被杀毒软件误报。
- 预计：Setup.exe 17.5 MB → 约 38–45 MB；便携 zip、两个 dmg 各增加约 20–30 MB；安装后占用增加约 80–90 MB。

## 阶段

- [pending] CI 打包：三个平台（win-amd64、darwin-arm64、darwin-amd64）下载官方 lite 版并校验哈希，放入安装包；附 OpenList 的 LICENSE 与源码链接。
- [pending] 进程管理：用 `openlist server --data <DATA_DIR>/openlist` 作为 `desktop.py` 管理的子进程，随托盘启动 / 退出 / 重启；取代内置模式下的 `alist start` 守护进程方式。
- [pending] 自动初始化：首次启动生成随机管理员密码（`openlist admin set`）写入 `.env`；`config.json` 监听改为 `127.0.0.1`；确认「签名所有」开启；自动填好 `ALIST_ORIGIN` / 账号。
- [pending] 引导与设置：`/setup` 第 2 步改为「使用内置 OpenList（推荐）/ 连接已有 AList」；内置模式下第 2 步变为「添加网盘」，打开 OpenList 存储管理页；设置页增加内置 / 外部切换。
- [pending] 兼容性验证：`connectors/alist.py` 在 OpenList v4 上的登录、列目录、`sign` 直链、`raw_url`（ffprobe）全部跑通。
- [pending] 预览版确认 → Squash 合并 → 正式发布。

## 约束

- 平台差异集中在 `desktop.py` / `services/alist_process.py`；路径统一从 `services/paths.py` 取，OpenList 数据放在 `paths.DATA_DIR/openlist`。
- 新增环境变量登记到 `services/settings.py` 的 `SECTIONS` 并补进 `.env.example`。
- 测试不启动真实进程，沿用 mock 方式。
- 不修改 OpenList 源码；如以后改为自编译，需公开修改后的源码（AGPL）。

## 待处理的边界情况

- 5244 端口已被用户自己的 AList / OpenList 占用：自动换端口，或引导改为「连接已有」。
- 升级 MovieDB 时替换 OpenList 程序但保留其数据目录。
- 「清除本地数据」要不要连同 OpenList 的存储配置一起清除，需在界面上说明。
- macOS：内嵌二进制需保留 Go 链接器生成的 ad-hoc 签名；首次放行仍按 README 的 `xattr` 说明。

## 分支

`feat/bundled-openlist`，按 CI 打包 → 进程管理 → 引导改造的顺序推进。
