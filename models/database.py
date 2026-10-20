import sqlite3
from pathlib import Path

from flask import current_app, g, has_app_context

from models.profiling import ProfiledConnection, init_profiler, is_profiling_enabled


def _connect(
    database_path: str | Path,
    *,
    mode: str,
    timeout: int = 10,
) -> sqlite3.Connection:
    database = Path(database_path).resolve()
    profile_app = current_app if has_app_context() else None
    factory = (
        ProfiledConnection
        if is_profiling_enabled(profile_app)
        else sqlite3.Connection
    )
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode={mode}",
        uri=True,
        timeout=timeout,
        factory=factory,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def connect_database(database_path: str | Path) -> sqlite3.Connection:
    return _connect(database_path, mode="ro")


def connect_writable(database_path: str | Path) -> sqlite3.Connection:
    return _connect(database_path, mode="rw")


def get_db() -> sqlite3.Connection:
    if "library_db" not in g:
        g.library_db = connect_database(current_app.config["DATABASE"])
    return g.library_db


def close_db(_error=None) -> None:
    connection = g.pop("library_db", None)
    if connection is not None:
        connection.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    init_profiler(app)
