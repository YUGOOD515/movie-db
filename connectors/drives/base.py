from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, ClassVar

import requests


def utc_iso(value) -> str:
    """把各网盘返回的时间统一成 UTC、毫秒精度的 ISO 字符串；无法解析时原样返回。

    同一个文件经内置驱动与 AList 读取时，时间写法不同（+00:00 与 +08:00、微秒与毫秒），
    不统一就会被误判为“源文件已修改”。
    """
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            moment = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        else:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def same_instant(left, right) -> bool:
    """两个时间是否表示同一时刻（容忍 1 秒内的精度差异）；任一方为空时不作判断。"""
    if not left or not right:
        return True
    try:
        parse = lambda value: datetime.fromisoformat(utc_iso(value).replace("Z", "+00:00"))  # noqa: E731
        return abs((parse(left) - parse(right)).total_seconds()) < 1
    except (TypeError, ValueError):
        return str(left) == str(right)


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    id: str
    size: int = 0
    modified: str = ""
    created: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    def __post_init__(self):
        # 所有驱动在这里统一时间写法，调用方无需关心来源
        object.__setattr__(self, "modified", utc_iso(self.modified))
        object.__setattr__(self, "created", utc_iso(self.created))


@dataclass(frozen=True)
class Link:
    url: str = ""
    headers: dict = field(default_factory=dict)
    expires_in: int | None = None
    proxy: bool = False
    concurrency: int = 0
    part_size: int = 0
    local_path: str = ""
    prefetch_size: int = 0


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"
    required: bool = False
    default: str = ""
    options: tuple = ()
    hint: str = ""
    advanced: bool = False


class DriveError(RuntimeError):
    pass


class AuthError(DriveError):
    pass


THROTTLED_MESSAGE = re.compile(r"频繁|过快|稍后再试|too many|rate.?limit|\b429\b", re.IGNORECASE)


class Drive:
    kind: ClassVar[str]
    label: ClassVar[str]
    fields: ClassVar[tuple[Field, ...]] = ()
    root_id: ClassVar[str] = ""

    def __init__(self, config: dict, save: Callable[[dict], None], session=None):
        self.config = dict(config)
        self.save = save
        self.session = session if session is not None else requests.Session()

    def root(self) -> Entry:
        return Entry(name="", is_dir=True, id=self.root_id)

    def check(self) -> str:
        raise NotImplementedError

    def list(self, directory: Entry) -> list[Entry]:
        raise NotImplementedError

    def link(self, file: Entry) -> Link:
        raise NotImplementedError
