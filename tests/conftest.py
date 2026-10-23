import pytest

from connectors import throttle


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch):
    """测试中不真正限速：保留重试 / 熔断逻辑，但去掉令牌桶等待与退避睡眠。"""
    fast = {name: throttle.Policy(rate=1e6, burst=1e6, concurrency=16, max_wait=policy.max_wait,
                                  attempts=policy.attempts)
            for name, policy in throttle._policies().items()}
    monkeypatch.setattr(throttle, "_policies", lambda: fast)
    monkeypatch.setattr(throttle, "_backoff", lambda attempt: 0.0)
    throttle.reset()
    from connectors import alist
    alist.forget_token()
    # 豆瓣默认开启：测试里关掉，避免录入用例真的访问豆瓣（需要的用例自行开启并打桩）
    monkeypatch.setenv("DOUBAN_ENABLED", "0")
    yield
    throttle.reset()
    alist.forget_token()
