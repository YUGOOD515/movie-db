import logging
import os
import subprocess
import time
from pathlib import Path

import requests

from connectors.alist import get_origin


logger = logging.getLogger(__name__)
REQUEST_TIMEOUT = 1
START_COMMAND_TIMEOUT = 10
STARTUP_TIMEOUT = 20
POLL_INTERVAL = 0.25


def is_alist_running(origin: str | None = None) -> bool:
    try:
        response = requests.get(
            (origin or get_origin()).rstrip("/"),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return False
    return response.status_code < 500


def start_alist(
    alist_dir: str | Path,
    *,
    origin: str | None = None,
    startup_timeout: float = STARTUP_TIMEOUT,
) -> bool:
    directory = Path(alist_dir).expanduser().resolve()
    executable = directory / "alist.exe"
    target_origin = (origin or get_origin()).rstrip("/")
    if is_alist_running(target_origin):
        return False
    if not executable.is_file():
        raise FileNotFoundError(f"未找到 AList 程序：{executable}")

    run_options = {
        "cwd": directory,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": START_COMMAND_TIMEOUT,
        "check": False,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_options["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run([str(executable), "start"], **run_options)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("AList 启动命令执行超时。") from error
    if result.returncode != 0:
        if is_alist_running(target_origin):
            return False
        message = (result.stderr or result.stdout or "未知错误").strip()
        raise RuntimeError(f"AList 启动命令失败：{message}")

    deadline = time.monotonic() + max(float(startup_timeout), 0)
    while time.monotonic() <= deadline:
        if is_alist_running(target_origin):
            return True
        time.sleep(POLL_INTERVAL)
    raise RuntimeError(f"AList 未在限定时间内响应：{target_origin}")


def start_configured_alist() -> bool:
    autostart = os.environ.get("ALIST_AUTOSTART", "1").strip().casefold()
    if autostart in {"0", "false", "no", "off"}:
        return False
    alist_dir = os.environ.get("ALIST_DIR", "").strip()
    if not alist_dir:
        return False
    try:
        return start_alist(alist_dir)
    except (OSError, RuntimeError, ValueError):
        logger.exception("AList 自动启动失败")
        return False
