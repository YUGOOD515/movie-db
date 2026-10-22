"""数据目录：选择顺序、旧版数据迁移（含失败回滚）与媒体文件的静态路由。"""

import importlib
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from services import paths


@pytest.fixture
def reload_paths(monkeypatch):
    """按给定的环境与安装布局重新计算 paths，测试结束后恢复原样。"""
    def load(*, packaged=False, portable=False, data_dir=None):
        monkeypatch.setattr(Path, "is_dir", lambda self, _real=Path.is_dir: (
            packaged if self == paths.RUNTIME_DIR else _real(self)))
        monkeypatch.setattr(Path, "is_file", lambda self, _real=Path.is_file: (
            portable if self == paths.APP_DIR.parent / "portable" else _real(self)))
        if data_dir is None:
            monkeypatch.delenv("MOVIEDB_DATA_DIR", raising=False)
        else:
            monkeypatch.setenv("MOVIEDB_DATA_DIR", str(data_dir))
        return importlib.reload(paths)

    yield load
    monkeypatch.undo()
    importlib.reload(paths)


def test_source_checkout_keeps_data_in_repository(reload_paths):
    module = reload_paths()
    assert module.DATA_DIR == module.APP_DIR
    assert module.MEDIA_ROOT == module.APP_DIR / "static"
    assert module.ENV_PATH == module.APP_DIR / ".env"


def test_installed_build_uses_user_data_directory(reload_paths):
    module = reload_paths(packaged=True)
    assert module.PACKAGED and not module.PORTABLE
    assert module.DATA_DIR == module.user_data_dir() != module.APP_DIR
    assert module.LOG_PATH == module.DATA_DIR / "logs" / "moviedb.log"


def test_portable_build_and_override(reload_paths, tmp_path):
    assert reload_paths(packaged=True, portable=True).DATA_DIR == paths.APP_DIR
    module = reload_paths(packaged=True, data_dir=tmp_path)
    assert module.DATA_DIR == tmp_path.resolve()
    assert module.database_path() == tmp_path.resolve() / "library.db"


def test_user_data_dir_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.user_data_dir() == tmp_path / "MovieDB"
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MACOS", True)
    assert paths.user_data_dir() == Path.home() / "Library" / "Application Support" / "MovieDB"


def _legacy(root: Path) -> None:
    (root / "static" / "images" / "movies").mkdir(parents=True)
    (root / "static" / "app.css").write_text("program file")
    (root / "static" / "images" / "movies" / "1.jpg").write_bytes(b"jpg")
    (root / "library.db").write_bytes(b"db")
    (root / ".env").write_text("ALIST_ORIGIN=http://127.0.0.1:5244\n")
    (root / "logs").mkdir()
    (root / "app.py").write_text("program file")


def test_migrate_legacy_moves_only_user_data(tmp_path):
    source, target = tmp_path / "app", tmp_path / "data"
    _legacy(source)
    (target / "logs").mkdir(parents=True)  # 新版先建好的空目录会让位
    moved = paths.migrate_legacy(source, target)
    assert set(moved) == {"library.db", ".env", "static/images", "logs"}
    assert (target / "static" / "images" / "movies" / "1.jpg").read_bytes() == b"jpg"
    assert (target / ".env").is_file() and not (source / ".env").exists()
    assert (source / "static" / "app.css").is_file() and (source / "app.py").is_file()
    assert paths.migrate_legacy(source, target) == []  # 只迁移一次


def test_migrate_legacy_skips_when_target_has_data(tmp_path):
    source, target = tmp_path / "app", tmp_path / "data"
    _legacy(source)
    target.mkdir()
    (target / "library.db").write_bytes(b"new")
    assert paths.migrate_legacy(source, target) == []
    assert (source / "library.db").read_bytes() == b"db"
    assert paths.migrate_legacy(source, source) == []


def test_migrate_legacy_rolls_back_on_failure(tmp_path):
    source, target = tmp_path / "app", tmp_path / "data"
    _legacy(source)
    real_move = shutil.move

    def move(src, dst):
        if src.endswith(".env"):
            raise PermissionError("文件被占用")
        return real_move(src, dst)

    with patch.object(paths.shutil, "move", side_effect=move), pytest.raises(OSError):
        paths.migrate_legacy(source, target)
    assert (source / "library.db").read_bytes() == b"db"
    assert not (target / "library.db").exists()


def test_media_files_are_served_from_data_directory(tmp_path):
    with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
        import app as movie_app
    media = tmp_path / "static"
    (media / "images" / "movies").mkdir(parents=True)
    (media / "images" / "movies" / "1.jpg").write_bytes(b"poster")
    original = movie_app.app.config["MEDIA_ROOT"]
    movie_app.app.config["MEDIA_ROOT"] = media
    try:
        client = movie_app.app.test_client()
        response = client.get("/static/images/movies/1.jpg")
        assert response.status_code == 200 and response.data == b"poster"
        response.close()
        assert client.get("/static/images/movies/missing.jpg").status_code == 404
        program = next(path for path in paths.APP_STATIC.glob("*.css"))
        response = client.get(f"/static/{program.name}")  # 程序自带的样式仍从程序目录读取
        assert response.status_code == 200
        response.close()
    finally:
        movie_app.app.config["MEDIA_ROOT"] = original
