import sqlite3
from pathlib import Path

from flask import current_app, g


def connect_database(database_path: str | Path) -> sqlite3.Connection:
    database = Path(database_path).resolve()
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


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
