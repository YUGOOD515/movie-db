from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, ClassVar

import requests


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    id: str
    size: int = 0
    modified: str = ""
    created: str = ""
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Link:
    url: str = ""
    headers: dict = field(default_factory=dict)
    expires_in: int | None = None
    proxy: bool = False
    concurrency: int = 0
    part_size: int = 0
    local_path: str = ""


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
