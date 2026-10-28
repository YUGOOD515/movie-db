"""Optional, request-local observation; never changes scheduling or retries."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

_observer = ContextVar("media_observer", default=None)


def safe_text(value, limit=180):
    text = re.sub(r"https?://\S+", "[链接]", str(value or ""))
    text = re.sub(r"(?i)\bBearer\s+[a-z0-9._~+/=-]+", "Bearer [隐藏]", text)
    text = re.sub(r'''(?i)(token|sign|authorization|api[_-]?key|password)["']?(\s*[:=]\s*)["']?[^\s,;"'}]+''', r"\1\2[隐藏]", text)
    return " ".join(text.split())[:limit]


def error_info(error):
    import requests
    code = getattr(error, "code", None)
    service = getattr(error, "service", "")
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if hasattr(error, "retry_after"):
        code = getattr(error, "reason", "rate_limit")
    elif isinstance(error, requests.Timeout):
        code = "timeout"
    elif isinstance(error, requests.ConnectionError):
        code = "connection"
    elif isinstance(error, FileNotFoundError):
        code = "not_found"
    elif isinstance(error, requests.HTTPError):
        code = {401:"unauthorized",403:"blocked",404:"not_found",429:"rate_limit",503:"busy"}.get(status,"http_error")
    reason = {"timeout": "请求超时", "connection": "连接失败", "rate_limit": "请求限流",
              "busy": "服务繁忙", "quota": "额度用尽", "blocked": "访问被拒绝",
              "not_found": "资源不存在", "expired": "链接已过期", "unauthorized":"认证失败", "http_error":"服务请求失败", "capacity":"队列等待超时"}.get(code)
    return {"code": code or "error", "service": service, "reason": reason or safe_text(error) or "处理失败"}


def emit(kind, **values):
    observer = _observer.get()
    if observer is not None:
        observer(kind, **values)


@contextmanager
def observe(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


@contextmanager
def activity(stage, key, label=""):
    emit("start", stage=stage, key=str(key), label=safe_text(label, 80))
    outcome = {"failed": False}
    try:
        yield outcome
    except Exception as error:
        emit("issue", stage=stage, label=safe_text(label, 80), **error_info(error))
        emit("end", stage=stage, key=str(key), failed=True)
        raise
    else:
        emit("end", stage=stage, key=str(key), failed=outcome["failed"])
