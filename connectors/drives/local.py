from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path, PurePosixPath

from connectors.drives.base import Drive, DriveError, Entry, Field, Link


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp).astimezone().isoformat()


class LocalDrive(Drive):
    kind = "local"
    label = "本机目录"
    fields = (Field("root", "目录", "path", required=True),)
    root_id = ""

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.directory = Path(str(config.get("root") or "")).expanduser()

    def _path(self, entry_id: str) -> Path:
        try:
            root = self.directory.resolve()
            path = (root / Path(*PurePosixPath(entry_id).parts)).resolve()
        except (OSError, RuntimeError, ValueError):
            raise DriveError("目录不存在或无法读取") from None
        if not path.is_relative_to(root):
            raise DriveError("路径超出挂载目录")
        return path

    def check(self) -> str:
        if not self.directory.is_dir() or not os.access(self.directory, os.R_OK):
            raise DriveError("目录不存在或无法读取")
        return f"已连接：{self.directory}"

    def list(self, directory: Entry) -> list[Entry]:
        path = self._path(directory.id)
        try:
            children = list(os.scandir(path))
        except OSError:
            raise DriveError("目录不存在或无法读取") from None
        result = []
        for child in children:
            if child.name.startswith("."):
                continue
            entry_id = PurePosixPath(directory.id) / child.name
            entry_id = "" if str(entry_id) == "." else str(entry_id)
            self._path(entry_id)
            try:
                stat = child.stat(follow_symlinks=True)
                is_dir = child.is_dir(follow_symlinks=True)
            except OSError:
                continue
            result.append(Entry(
                name=child.name,
                is_dir=is_dir,
                id=entry_id,
                size=0 if is_dir else stat.st_size,
                modified=_iso(stat.st_mtime),
                created=_iso(stat.st_ctime),
            ))
        return sorted(result, key=lambda entry: entry.name.casefold())

    def link(self, file: Entry) -> Link:
        return Link(local_path=str(self._path(file.id)))
