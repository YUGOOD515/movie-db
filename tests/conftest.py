import pytest
import requests
from urllib.parse import urlparse

from connectors import throttle
from services import import_runtime


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch, tmp_path):
    """测试中不真正限速：保留重试 / 熔断逻辑，但去掉令牌桶等待与退避睡眠。"""
    fast = {name: throttle.Policy(rate=1e6, burst=1e6, concurrency=16, max_wait=policy.max_wait,
                                  attempts=policy.attempts)
            for name, policy in throttle._policies().items()}
    monkeypatch.setattr(throttle, "_policies", lambda: fast)
    monkeypatch.setattr(throttle, "_backoff", lambda attempt: 0.0)
    monkeypatch.setenv("MOVIEDB_API_USAGE_DATABASE", str(tmp_path / "api-usage.db"))
    def unexpected_network(*args, **kwargs):
        url = kwargs.get("url") or (args[2] if len(args) > 2 else "")
        if urlparse(url).hostname in ("localhost", "127.0.0.1", "::1"):
            raise requests.ConnectionError("Local services are offline in tests")
        raise AssertionError("External HTTP requests must be mocked in tests")
    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_network)
    throttle.reset()
    from connectors import alist
    alist.forget_token()
    # 豆瓣默认开启：测试里关掉，避免录入用例真的访问豆瓣（需要的用例自行开启并打桩）
    monkeypatch.setenv("DOUBAN_ENABLED", "0")
    yield
    import_runtime.reset()
    throttle.reset()
    alist.forget_token()
