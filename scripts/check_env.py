"""核对本地 .env 与 .env.example 的变量名是否同步（只比较名称，不输出任何值）。

用法：uv run python scripts/check_env.py
- 缺少：.env.example 中未注释的变量（必填项），.env 里没有。
- 未登记：.env 里有、.env.example 里完全没有的变量（可能已废弃，或需要补进示例）。
- 可选未设置：.env.example 中注释掉的可选项，.env 未设置，供了解有哪些新开关。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
COMMENTED = re.compile(r"^\s*#\s*([A-Z][A-Z0-9_]*)\s*=")


def read(path: Path) -> tuple[list[str], list[str]]:
    active, commented = [], []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if match := ACTIVE.match(line):
            active.append(match.group(1))
        elif match := COMMENTED.match(line):
            commented.append(match.group(1))
    return active, commented


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from services import paths

    env_path, example_path = paths.ENV_PATH, paths.ENV_EXAMPLE
    if not env_path.exists():
        print("未找到 .env，可复制 .env.example 后填写。")
        return 1
    local, _ = read(env_path)
    required, optional = read(example_path)
    known = set(required) | set(optional)
    missing = [name for name in required if name not in local]
    unknown = [name for name in local if name not in known]
    unset = [name for name in dict.fromkeys(optional) if name not in local and name not in required]
    duplicated = sorted({name for name in local if local.count(name) > 1})

    def show(title: str, names: list[str]) -> None:
        print(f"{title}（{len(names)}）" + ("：" if names else ""))
        for name in names:
            print(f"  {name}")

    show("缺少（示例中未注释的变量）", missing)
    show("未登记（示例中没有，可能已废弃或需补进示例）", unknown)
    if duplicated:
        show(".env 中重复", duplicated)
    if unset:
        print(f"可选未设置（{len(unset)}）：" + "、".join(unset))
    if not (missing or unknown or duplicated):
        print("✓ .env 与 .env.example 已同步。")
    return 1 if missing or duplicated else 0


if __name__ == "__main__":
    sys.exit(main())
