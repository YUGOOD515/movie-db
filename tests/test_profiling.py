import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from models.database import connect_database
from models.profiling import ProfiledConnection, init_profiler


class ProfilingTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        with closing(sqlite3.connect(self.database_path)) as database:
            database.execute("CREATE TABLE sample (value INTEGER)")
            database.execute("INSERT INTO sample VALUES (1)")
            database.commit()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_testing_mode_profiles_requests_and_connections(self):
        app = Flask(__name__)
        app.testing = True
        init_profiler(app)

        @app.get("/")
        def index():
            with closing(connect_database(self.database_path)) as database:
                self.assertIsInstance(database, ProfiledConnection)
                database.execute("SELECT value FROM sample").fetchone()
            return "ok"

        output = StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("sql_execute_time", output.getvalue())
        self.assertIn("SELECT value FROM sample", output.getvalue())

    def test_production_uses_plain_sqlite_connection(self):
        app = Flask(__name__)
        with patch.dict(os.environ, {}, clear=True), app.app_context(), closing(
            connect_database(self.database_path)
        ) as database:
            self.assertNotIsInstance(database, ProfiledConnection)


if __name__ == "__main__":
    unittest.main()
