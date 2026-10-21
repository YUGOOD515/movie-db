"""外部接口的统一限速与退避。

免费 API Key 普遍有每秒 / 每分钟 / 每日三类上限：
- TMDB：官方未公布固定配额，上限约 40 req/s，超出返回 429。
- AList：本身不限流，但夸克等网盘驱动有风控，过快会返回“请求太频繁”之类的错误。
- LLM（OpenAI 兼容）：免费档通常是每分钟十几到几十次、每日几十到上千次，超出返回 429，
  并在 Retry-After / X-RateLimit-Reset / retryDelay 中给出恢复时间。
  DeepSeek 官方不限每分钟次数，只限账号并发（deepseek-flash 2500、deepseek-v4-pro 500），
  超出返回 429，服务器繁忙返回 503；因此只限并发（默认 4，LLM_CONCURRENCY），忽略 LLM_RPM。
- MDBList：免费档每日 1000 次，且每 5 分钟最多 1000 次读取。

每个服务一只令牌桶（平均速率）加一个信号量（同时在途请求数）。遇到 429/503 时按服务端给出的
等待时间（否则指数退避 + 抖动）全服务暂停后重试；需要等待的时间超过 max_wait（多半是每日额度
用尽）时不再阻塞，改为“熔断”到恢复时间，期间同一服务的请求直接抛出 RateLimitError，由调用方
降级处理（例如 AI 识别退回规则识别）。

调用方式：throttle.call("tmdb", lambda: session.get(...))。被包装的仍是原来的 requests 调用，
测试里对 requests 的打桩不受影响。
"""

from __future__ import annotations

import email.utils
import logging
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import requests


logger = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 503})


class RateLimitError(requests.RequestException):
    """服务限流且短时间内不会恢复（如每日额度用尽）。"""

    def __init__(self, service: str, retry_after: float | None = None):
        self.service = service
        self.retry_after = retry_after
        wait = f"，约 {_human(retry_after)}后恢复" if retry_after else ""
        label = LABELS.get(service, service)
        gap = " " if label[-1].isascii() else ""
        super().__init__(f"{label}{gap}请求过于频繁或额度已用尽{wait}。")


LABELS = {"tmdb": "TMDB", "tmdb-image": "TMDB 图片", "alist": "AList", "llm": "AI 接口", "mdblist": "MDBList"}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _human(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1, round(seconds))} 秒"
    if seconds < 5400:
        return f"{round(seconds / 60)} 分钟"
    return f"{round(seconds / 3600, 1):g} 小时"


@dataclass(frozen=True)
class Policy:
    rate: float          # 每秒平均请求数
    burst: float         # 令牌桶容量（允许的瞬时突发）
    concurrency: int     # 同时在途请求数
    max_wait: float      # 单次愿意等待恢复的最长秒数，超过即熔断
    attempts: int = 4    # 含首次在内的最多尝试次数


def _llm_policy() -> Policy:
    base_url = os.environ.get("LLM_BASE_URL", "")
    deepseek = re.search(r"//([a-z0-9-]+\.)*deepseek\.com(?:[:/]|$)", base_url.strip().lower()) is not None
    if deepseek:
        # 按并发限流：速率只做防抖（每秒 20 次），真正的约束是同时在途数。
        return Policy(rate=20, burst=4, concurrency=int(_env_float("LLM_CONCURRENCY", 4)), max_wait=90)
    return Policy(rate=_env_float("LLM_RPM", 10) / 60, burst=1,
                  concurrency=int(_env_float("LLM_CONCURRENCY", 2)), max_wait=90)


def _policies() -> dict[str, Policy]:
    return {
        "tmdb": Policy(rate=20, burst=10, concurrency=8, max_wait=30),
        "tmdb-image": Policy(rate=20, burst=10, concurrency=6, max_wait=30),
        "alist": Policy(
            # 目录读取多数命中 AList 自身缓存，速率不是网盘风控的关键；同时在途数才是，保持 4。
            rate=_env_float("ALIST_RATE", 20),
            burst=8,
            concurrency=int(_env_float("ALIST_CONCURRENCY", 4)),
            max_wait=30,
        ),
        "llm": _llm_policy(),
        "mdblist": Policy(rate=3, burst=3, concurrency=2, max_wait=60),
    }


class Limiter:
    def __init__(self, name: str, policy: Policy):
        self.name = name
        self.policy = policy
        self._lock = threading.Lock()
        self._tokens = policy.burst
        self._updated = time.monotonic()
        self._paused_until = 0.0
        self._slots = threading.BoundedSemaphore(max(1, policy.concurrency))

    def pause(self, seconds: float) -> None:
        """整个服务暂停 seconds 秒（所有线程共享）。"""
        with self._lock:
            self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    def blocked_for(self) -> float:
        with self._lock:
            return max(0.0, self._paused_until - time.monotonic())

    def _take(self) -> float:
        """取一个令牌；返回还需等待的秒数（0 表示已取到）。"""
        with self._lock:
            now = time.monotonic()
            if now < self._paused_until:
                return self._paused_until - now
            self._tokens = min(self.policy.burst, self._tokens + (now - self._updated) * self.policy.rate)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            return (1 - self._tokens) / self.policy.rate

    @contextmanager
    def slot(self):
        blocked = self.blocked_for()
        if blocked > self.policy.max_wait:
            raise RateLimitError(self.name, blocked)
        with self._slots:
            while (delay := self._take()) > 0:
                time.sleep(min(delay, 5))
            yield


_limiters: dict[str, Limiter] = {}
_limiters_lock = threading.Lock()


def limiter(service: str) -> Limiter:
    with _limiters_lock:
        if service not in _limiters:
            _limiters[service] = Limiter(service, _policies()[service])
        return _limiters[service]


def reset() -> None:
    """清空全部限速状态（测试或修改环境变量后使用）。"""
    with _limiters_lock:
        _limiters.clear()


_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?$")
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def _parse_seconds(value: str | None) -> float | None:
    """解析 Retry-After（秒或 HTTP 日期）、X-RateLimit-Reset（秒 / 毫秒时间戳）、“2m59.5s”。"""
    if not value:
        return None
    value = value.strip()
    try:
        number = float(value)
    except ValueError:
        number = None
    if number is not None:
        now = time.time()
        if number > 1e12:            # 毫秒时间戳
            return max(0.0, number / 1000 - now)
        if number > 1e9:             # 秒时间戳
            return max(0.0, number - now)
        return max(0.0, number)
    match = _DURATION.match(value)
    if match and any(match.groups()):
        hours, minutes, seconds = (float(part or 0) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, moment.timestamp() - time.time())


def retry_after(response) -> float | None:
    headers = getattr(response, "headers", None) or {}
    for name in ("Retry-After", "X-RateLimit-Reset", "x-ratelimit-reset-requests"):
        seconds = _parse_seconds(headers.get(name))
        if seconds is not None:
            return seconds
    try:
        match = _RETRY_DELAY.search(response.text[:4000])
    except Exception:
        match = None
    return float(match.group(1)) if match else None


def _backoff(attempt: int) -> float:
    return min(30.0, 2 ** attempt) * (0.5 + random.random() / 2)


def call(service: str, send, *, throttled=None):
    """在服务的速率与并发限制内执行 send()（返回 requests.Response）。

    throttled(response) 可识别“HTTP 200 但业务上被限流”的响应（如 AList 驱动风控），
    返回 True 时按 429 处理。连接失败会退避重试；超时直接抛出，避免长时间占用槽位。
    """
    gate = limiter(service)
    policy = gate.policy
    for attempt in range(policy.attempts):
        last = attempt == policy.attempts - 1
        with gate.slot():
            try:
                response = send()
            except requests.ConnectionError:
                if last:
                    raise
                response = None
        if response is None:
            time.sleep(_backoff(attempt))
            continue
        limited = response.status_code in RETRY_STATUS or bool(throttled and throttled(response))
        if not limited:
            return response
        delay = retry_after(response)
        wait = delay if delay is not None else _backoff(attempt)
        response.close()
        gate.pause(wait)
        if wait > policy.max_wait or last:
            # 每日额度用尽或持续限流：服务暂停到恢复时间，期间请求直接降级而不是排队等待。
            logger.warning("%s 限流，暂停约 %s", service, _human(wait))
            raise RateLimitError(service, wait)
        logger.info("%s 限流，%.1f 秒后重试（第 %d 次）", service, wait, attempt + 1)
    raise RateLimitError(service)  # pragma: no cover - 循环必然返回或抛出


def ffprobe_workers() -> int:
    """并行 ffprobe 的进程数：每个进程都会经 AList 读取网盘文件头。"""
    return max(1, min(8, int(_env_float("FFPROBE_CONCURRENCY", 3))))
