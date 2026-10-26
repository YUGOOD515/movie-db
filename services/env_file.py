"""读写项目根目录的 .env：只改动指定变量，保留原有顺序、注释与其他变量。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
# 模板里被注释掉的示例行，如 “# TMDB_PROXY=”：新增变量时写在这里，保持与说明相邻。
COMMENTED = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)=")
SAFE = re.compile(r"^[A-Za-z0-9_./:@%+,=~\-]*$")


def read(path: Path) -> dict[str, str]:
    """读取 .env 文件中的键值映射，文件不存在或无法读取时返回空字典。"""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values

        return {key: (value or "").strip() for key, value in dotenv_values(path).items() if key}
    except (ImportError, OSError):
        return {}


def quote(value: str) -> str:
    """python-dotenv 可解析的写法：简单值原样写入，其他用引号包住。"""
    if SAFE.fullmatch(value):
        return value
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def update(path: Path, values: dict[str, str | None], template: Path | None = None) -> None:
    """values 中为 None 的变量被删除；文件不存在时以 template（.env.example）为底稿。"""
    path = Path(path)
    if path.exists():
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    elif template is not None and Path(template).exists():
        lines = Path(template).read_text(encoding="utf-8-sig").splitlines()
    else:
        lines = []
    pending = dict(values)
    active = {match.group(1) for match in map(LINE.match, lines) if match}
    output = []
    for line in lines:
        match = LINE.match(line)
        name = match.group(1) if match else None
        commented = COMMENTED.match(line) if match is None else None
        if commented and commented.group(1) not in active and pending.get(commented.group(1)) is not None:
            name = commented.group(1)
            output.append(line)
            output.append(f"{name}={quote(pending.pop(name))}")
            continue
        if name in pending:
            value = pending.pop(name)
            if value is not None:
                output.append(f"{name}={quote(value)}")
            continue
        if name is not None and name in values:
            continue  # 同名变量重复出现：只保留第一处
        output.append(line)
    additions = [f"{name}={quote(value)}" for name, value in pending.items() if value is not None]
    if additions:
        if output and output[-1].strip():
            output.append("")
        output.extend(additions)
    text = "\n".join(output).rstrip("\n") + "\n"
    # 先写临时文件再替换，写到一半断电也不会留下残缺的配置。
    handle, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
