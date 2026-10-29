# 进度

每完成一个阶段补一条：日期、PR、完成情况、与计划的偏差及原因、遗留问题。

| 阶段 | 状态 | PR | 备注 |
|---|---|---|---|
| P0 许可证 | 完成（待合并） | 分支 `chore/agpl-license` | 2026-10-29：新增 `LICENSE`（AGPL-3.0 全文，取自 gnu.org）与 `NOTICE`（说明 `connectors/drives/` 参照 OpenList 改写），`pyproject.toml` 声明 `license = "AGPL-3.0-or-later"`，README 中英文许可段改为 AGPL。测试全绿（276 passed, 1 skipped），`uv lock --check` 通过。偏差：① README 原本写「尚未选择许可证」，保留原段改写而非另起一行；② `license-files` 未显式声明，PEP 639 默认已收录 `LICENSE` / `NOTICE`。遗留：安装包是否最终随附这两个文件未验证（按 `packaging/build_windows.py` 的排除清单应当会带上，未实机构建确认）。 |
| P1 存储分层 | 待开始 | | |
| P2 核心层 + 本机驱动 + 播放转发 | 待开始 | | |
| P3 切换调用点 + AList 透传驱动 | 待开始 | | |
| P4 网盘页与首次引导 | 待开始 | | |
| P5 夸克 / UC | 待开始 | | |
| P6 阿里云盘 Open | 待开始 | | |
| P7 百度网盘 | 待开始 | | |
| P8 123 云盘 Open | 待开始 | | |
| P9 文档 | 待开始 | | |
