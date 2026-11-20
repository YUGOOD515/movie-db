# 任务：播放器优先使用安装包内置的 mpv，并让 Windows 上的 mpv 也能记录观看进度

## 背景

MovieDB 是 Flask 写的本地影视管理应用，打包成 Windows 安装包和 macOS .app。下一个版本会在安装包里内置官方 mpv（打包脚本由别的任务负责，本任务不管）：

- Windows：`<RUNTIME_DIR>/mpv/mpv.exe`
- macOS：`<RUNTIME_DIR>/mpv/mpv.app/Contents/MacOS/mpv`
- MovieDB 自带的 mpv 配置目录：`<RUNTIME_DIR>/mpv/config/`

`RUNTIME_DIR` 已经定义在 `services/paths.py` 里。源码运行时这个目录不存在，所以“没有内置 mpv”是正常情况。

`connectors/player.py` 负责选择和启动播放器，并在后台线程里记录进度。现状和问题：

- mpv / IINA 通过 JSON IPC（`--input-ipc-server`）读取进度，但 `_MpvClient` 只支持 Unix 套接字。`play_video` 里写了 `ipc = None if IS_WINDOWS else ...`，所以 **Windows 上用 mpv 不记录进度**。
- 自动选择的顺序：Windows 是 mpc-hc → mpv → vlc，macOS 是 iina → mpv → vlc，找不到时用系统默认程序。

先通读 `connectors/player.py` 和 `tests/test_player.py`，再动手。

## 要改的文件

- `services/paths.py`：新增两个路径常量。
- `connectors/player.py`：内置 mpv 的查找和优先级、Windows 命名管道 IPC、内置 mpv 的配置目录参数。
- `tests/test_player.py`：补充测试。

不要改其他文件（包括 `app.py`、`services/setup_wizard.py`、`services/settings.py`、`packaging/`、文档）。

## 接口契约

### `services/paths.py`

```python
BUNDLED_MPV = RUNTIME_DIR / "mpv" / ("mpv.exe" if IS_WINDOWS else "mpv.app/Contents/MacOS/mpv")
BUNDLED_MPV_CONFIG = RUNTIME_DIR / "mpv" / "config"
```

拼路径时按 `Path` 的写法分段，不要在字符串里写 `/`。参照同文件里的 `BUNDLED_FFPROBE`。

### `connectors/player.py`

1. 新增常量 `BUNDLED_MPV_MIN_MACOS = (15, 0)`。
2. 新增 `bundled_mpv() -> str | None`：
   - 每次调用时读取 `paths.BUNDLED_MPV`（不要在导入模块时缓存），文件存在才返回 `str(路径)`，否则返回 `None`；
   - macOS 上，用 `platform.mac_ver()[0]` 取系统版本，取主版本号和次版本号组成元组，低于 `BUNDLED_MPV_MIN_MACOS` 时返回 `None`。版本号取不到或解析失败时，按“可以运行”处理；
   - 读取版本号的代码单独写成一个小函数，方便测试时替换。
3. 新增 `is_bundled(path: str | None) -> bool`：`path` 不为空，并且和 `paths.BUNDLED_MPV` 指向同一个文件时返回 `True`。用 `Path(...).resolve()` 比较，路径不存在时不能抛异常。
4. `resolve_player(overrides=None)` 的新规则（`PLAYER_PATH` 去掉首尾空白后不为空，就算“填了”）：

   | PLAYER | PLAYER_PATH | 结果 |
   |---|---|---|
   | 空或 `auto` | 填了 | 不变：用填写的路径 |
   | 空或 `auto` | 没填 | **先看 `bundled_mpv()`，有就返回 `("mpv", 路径)`**；没有时按原来的 `_auto_order()` |
   | `mpv` | 填了 | 不变：用填写的路径 |
   | `mpv` | 没填 | **先看 `bundled_mpv()`**；没有时按原来的候选列表 |
   | 其他值 | — | 不变 |

5. `play_video(...)` 的签名和返回值不变。`kind == "mpv"` 时：
   - **所有平台都开 IPC**。Windows 的地址是 `\\.\pipe\moviedb-mpv-<os.getpid()>-<generation>`，写成新函数 `_pipe_name(generation)`；其他平台沿用 `_ipc_path(generation)`；
   - `is_bundled(path)` 为真、并且 `paths.BUNDLED_MPV_CONFIG` 是目录时，加上参数 `--config-dir=<该目录的字符串>`，放在 `--force-window=immediate` 和 `--start=...` 之后、`--input-ipc-server` 之前；
   - 其余参数和顺序不变：`[path, "--force-window=immediate", "--start=...", (--config-dir), "--input-ipc-server=...", *--sub-file=..., video_url]`；
   - 有 `progress_callback` 时启动 `_monitor_mpv_playback`（现在 Windows 上不会启动，改完后要启动）。
6. `kind == "iina"` 的行为不变：IINA 只在 macOS 上运行，Windows 上仍然不开 IPC。
7. `_MpvClient(path)`：
   - 非 Windows 保持现在的 Unix 套接字实现；
   - Windows 用标准库打开命名管道，例如 `open(path, "r+b", buffering=0)`，按行读写 UTF-8 JSON，和套接字版本的协议一样（一行一条消息，用 `request_id` 匹配回复，跳过事件通知）；
   - 管道还不存在时，构造函数抛 `OSError`（含 `FileNotFoundError`），由 `_monitor_mpv_playback` 现有的重试逻辑和 `MPC_START_TIMEOUT` 截止时间处理；
   - `get(name)`、`close()` 的语义不变；mpv 退出后读写抛 `OSError`、`ValueError`，或者读到 EOF（抛 `ConnectionError`，它是 `OSError` 的子类），监控线程据此结束；
   - 不要引入 pywin32 等第三方包。
8. `_monitor_mpv_playback`：结束时只在非 Windows 上删除 IPC 路径（命名管道不是文件，不能 `unlink`）。
9. 模块开头的说明文字更新一句：内置 mpv 优先，Windows 用命名管道。

## 语义细节

- “内置 mpv 存在”只看 `paths.BUNDLED_MPV` 这个文件，不检查 `config` 目录。没有 `config` 目录时照样用内置 mpv，只是不加 `--config-dir`。
- 用户自己装的 mpv（来自 `_candidates("mpv")` 或 `PLAYER_PATH`）**永远不加** `--config-dir`，即使它恰好在 `runtime` 目录附近。
- `PLAYER=mpc-hc`、`vlc`、`iina`、`system` 时，不管有没有内置 mpv，结果都和现在一样。
- `TRACKS_PROGRESS`、`PLAYER_NAMES`、`label()` 不变。

## 测试（`tests/test_player.py`）

现有测试要全部通过，不要删、不要放宽。只允许以下两处调整：

- `no_players` 夹具：再把 `player.bundled_mpv` 替换成返回 `None` 的函数，避免受本机文件影响；
- `test_launch_arguments` 里“是否开 IPC”的期望：改成 mpv 在所有平台都开，iina 只在非 Windows 上开，vlc 都不开。

新增的测试必须覆盖：

1. 有内置 mpv、没填 `PLAYER_PATH` 时，自动选择返回 `("mpv", 内置路径)`，即使 `_candidates` 能找到 mpc-hc。
2. 没有内置 mpv 时，自动选择按 `_auto_order()` 的顺序（沿用现有写法，monkeypatch `_auto_order`）。
3. 填了 `PLAYER_PATH` 时，`PLAYER` 为空和为 `mpv` 这两种情况都返回填写的路径，不返回内置路径。
4. `PLAYER=mpv`、没填路径时，先返回内置路径；没有内置时返回 `_candidates("mpv")` 里的路径。
5. `PLAYER=mpc-hc` 时，有内置 mpv 也返回 mpc-hc。
6. macOS 版本判断：monkeypatch 读取版本号的小函数和平台判断，分别验证 `"14.6"` → `None`、`"15.0"` → 路径、`"26.1"` → 路径、`""` → 路径。
7. `is_bundled`：同一文件的两种写法（比如带 `..` 的路径）返回 `True`，其他文件返回 `False`，`None` 和不存在的路径返回 `False` 且不报错。
8. 启动参数：内置 mpv 并且配置目录存在 → 有 `--config-dir=...`，位置符合契约；配置目录不存在 → 没有；用户自己的 mpv → 没有。用 monkeypatch 把 `paths.BUNDLED_MPV` 和 `paths.BUNDLED_MPV_CONFIG` 指到 `tmp_path`。
9. 在 Windows 上（monkeypatch `player.IS_WINDOWS = True` 即可，不需要真的在 Windows 上），mpv 的参数里有 `--input-ipc-server=\\.\pipe\moviedb-mpv-<pid>-<generation>`，并且启动了监控线程。
10. **真实的命名管道测试**（只在 Windows 上跑，用 `pytest.mark.skipif(os.name != "nt", ...)`）：
    - 只用标准库建一个假的 mpv 服务端，比如 `multiprocessing.connection.Listener(address=<管道名>, family="AF_PIPE")`，或者 `_winapi`；
    - 对 `get_property time-pos` 回 `12.5`，对 `duration` 回 `100`，回复里带上请求的 `request_id`，并且在回复之前先插一条事件通知（不带 `request_id`），验证客户端会跳过它；
    - 验证进度回调收到 `(12500, 100000)`；
    - 服务端关闭后，`_monitor_mpv_playback` 在 5 秒内返回；
    - 把 `IPC_POLL_INTERVAL` 调小，测试总耗时控制在 10 秒以内。
11. Windows 上 `_monitor_mpv_playback` 结束时不会对管道路径调用 `unlink`（monkeypatch `IS_WINDOWS` 并替换 `Path.unlink` 来断言）。

## 约束

- Python 3.13，只用标准库和项目已有的依赖。
- 代码要能在 Windows 和 macOS 上运行；路径用 `pathlib`。
- 风格跟随现有代码：注释和面向用户的文字用中文，日志用英文。
- 只做契约里写的改动，不要顺手重构，不要加配置项。

## 验收标准

1. 在工作树里运行 `.venv\Scripts\python.exe -m pytest -q tests/test_player.py`，全部通过；Windows 上第 10 项不能被跳过。
2. `.venv\Scripts\python.exe -m pytest -q` 全部通过（整个测试套件）。
3. 汇报里列出：改了哪些函数；按契约逐条说明是怎么实现的；单独写一节“假设与疑问”，列出规格里你觉得有歧义、自己做了判断的地方。
