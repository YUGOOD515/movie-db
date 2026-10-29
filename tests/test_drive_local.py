from datetime import datetime

import pytest

from connectors.drives.base import DriveError, Entry
from connectors.drives.local import LocalDrive


def test_local_list_check_and_link(tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "movie.mkv").write_bytes(b"video")
    (tmp_path / ".hidden").write_bytes(b"hidden")
    drive = LocalDrive({"root": str(tmp_path)}, lambda config: None)

    assert drive.check() == f"已连接：{tmp_path}"
    root = drive.root()
    entries = drive.list(root)
    assert [entry.name for entry in entries] == ["folder"]
    folder = entries[0]
    assert folder.is_dir and folder.id == "folder"
    file = drive.list(folder)[0]
    assert file.id == "folder/movie.mkv" and file.size == 5
    assert datetime.fromisoformat(file.modified).tzinfo is not None
    assert datetime.fromisoformat(file.created).tzinfo is not None
    assert drive.link(file).local_path == str(tmp_path / "folder" / "movie.mkv")


def test_local_rejects_parent_and_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"outside")
    drive = LocalDrive({"root": str(root)}, lambda config: None)
    with pytest.raises(DriveError, match="路径超出挂载目录"):
        drive.list(Entry(name="", is_dir=True, id="../"))

    try:
        (root / "escape.mkv").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不能创建符号链接")
    with pytest.raises(DriveError, match="路径超出挂载目录"):
        drive.list(drive.root())
    with pytest.raises(DriveError, match="路径超出挂载目录"):
        drive.link(Entry(name="escape.mkv", is_dir=False, id="escape.mkv"))
