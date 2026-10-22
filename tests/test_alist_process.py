import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from services import alist_process


class AListProcessTest(unittest.TestCase):
    def test_running_probe_accepts_an_http_response(self):
        response = Mock(status_code=200)
        with patch.object(alist_process.requests, "get", return_value=response) as get:
            self.assertTrue(alist_process.is_alist_running("http://127.0.0.1:5244/"))

        get.assert_called_once_with(
            "http://127.0.0.1:5244",
            timeout=alist_process.REQUEST_TIMEOUT,
        )

    def test_running_probe_handles_connection_failure(self):
        with patch.object(
            alist_process.requests,
            "get",
            side_effect=requests.ConnectionError,
        ):
            self.assertFalse(alist_process.is_alist_running())

    def test_start_skips_an_existing_instance(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(alist_process, "is_alist_running", return_value=True), patch.object(
                alist_process.subprocess, "run"
            ) as run:
                started = alist_process.start_alist(temporary_directory)

        self.assertFalse(started)
        run.assert_not_called()

    def test_start_uses_silent_command_and_waits_until_ready(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory).resolve()
            executable = directory / alist_process.EXECUTABLE
            executable.touch()
            completed = subprocess.CompletedProcess(
                [str(executable), "start"],
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch.object(
                alist_process,
                "is_alist_running",
                side_effect=[False, True],
            ), patch.object(
                alist_process.subprocess,
                "run",
                return_value=completed,
            ) as run:
                started = alist_process.start_alist(
                    directory,
                    origin="http://127.0.0.1:5244",
                )

        self.assertTrue(started)
        command, options = run.call_args
        self.assertEqual(command[0], [str(executable), "start"])
        self.assertEqual(options["cwd"], directory)
        self.assertTrue(options["capture_output"])
        self.assertFalse(options["check"])

    def test_start_tolerates_a_reload_race(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = Path(temporary_directory).resolve() / alist_process.EXECUTABLE
            executable.touch()
            completed = subprocess.CompletedProcess(
                [str(executable), "start"],
                returncode=1,
                stdout="",
                stderr="already running",
            )
            with patch.object(
                alist_process,
                "is_alist_running",
                side_effect=[False, True],
            ), patch.object(
                alist_process.subprocess,
                "run",
                return_value=completed,
            ):
                self.assertFalse(alist_process.start_alist(temporary_directory))

    def test_start_rejects_a_directory_without_alist_exe(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(alist_process, "is_alist_running", return_value=False):
                with self.assertRaisesRegex(FileNotFoundError, alist_process.EXECUTABLE):
                    alist_process.start_alist(temporary_directory)

    def test_configured_start_is_opt_in_and_non_fatal(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            alist_process, "start_alist"
        ) as start:
            self.assertFalse(alist_process.start_configured_alist())
            start.assert_not_called()

        with patch.dict(os.environ, {"ALIST_DIR": r"C:\missing-alist"}, clear=True), patch.object(
            alist_process,
            "start_alist",
            side_effect=RuntimeError("failed"),
        ), self.assertLogs(alist_process.logger, level="ERROR"):
            self.assertFalse(alist_process.start_configured_alist())

    def test_configured_start_can_be_disabled(self):
        environment = {
            "ALIST_AUTOSTART": "false",
            "ALIST_DIR": r"C:\alist",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            alist_process, "start_alist"
        ) as start:
            self.assertFalse(alist_process.start_configured_alist())
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
