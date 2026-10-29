import email.utils
import time
import unittest
from unittest.mock import Mock, patch

import requests

from connectors import llm, throttle
from connectors.drives.alist import AListDrive
from connectors.drives.base import Entry
from services import recognition


def response(status=200, headers=None, payload=None, text=""):
    item = Mock(status_code=status, headers=headers or {}, text=text)
    item.json.return_value = payload if payload is not None else {}
    return item


class RetryAfterTest(unittest.TestCase):
    def test_parses_common_reset_formats(self):
        self.assertEqual(throttle.retry_after(response(headers={"Retry-After": "7"})), 7)
        date = email.utils.formatdate(time.time() + 120, usegmt=True)
        self.assertAlmostEqual(throttle.retry_after(response(headers={"Retry-After": date})), 120, delta=2)
        reset_ms = str(int((time.time() + 60) * 1000))
        self.assertAlmostEqual(throttle.retry_after(response(headers={"X-RateLimit-Reset": reset_ms})), 60, delta=2)
        self.assertAlmostEqual(throttle.retry_after(response(headers={"x-ratelimit-reset-requests": "2m59.5s"})), 179.5)
        gemini = '{"error": {"details": [{"@type": "RetryInfo", "retryDelay": "37s"}]}}'
        self.assertEqual(throttle.retry_after(response(text=gemini)), 37)
        self.assertIsNone(throttle.retry_after(response()))


class CallTest(unittest.TestCase):
    def test_retries_short_rate_limit_then_succeeds(self):
        send = Mock(side_effect=[response(429, {"Retry-After": "0"}), response(200)])
        self.assertEqual(throttle.call("tmdb", send).status_code, 200)
        self.assertEqual(send.call_count, 2)

    def test_daily_quota_trips_circuit_and_fails_fast(self):
        send = Mock(return_value=response(429, {"Retry-After": "3600"}))
        with self.assertRaises(throttle.RateLimitError) as caught:
            throttle.call("llm", send)
        self.assertEqual(str(caught.exception), "AI 接口请求过于频繁或额度已用尽，约 60 分钟后恢复。")
        with self.assertRaises(throttle.RateLimitError):
            throttle.call("llm", send)
        self.assertEqual(send.call_count, 1)  # 熔断期间不再发请求
        self.assertIsInstance(caught.exception, requests.RequestException)

    def test_token_bucket_spaces_requests(self):
        gate = throttle.Limiter("x", throttle.Policy(rate=20, burst=1, concurrency=1, max_wait=5))
        started = time.monotonic()
        for _ in range(3):
            with gate.slot():
                pass
        self.assertGreaterEqual(time.monotonic() - started, 0.09)

    def test_concurrency_halves_on_429_and_recovers(self):
        gate = throttle.Limiter("x", throttle.Policy(rate=1e6, burst=1e6, concurrency=8, max_wait=5))
        gate.limited()
        gate.limited()
        self.assertEqual(gate.concurrency, 2)
        for _ in range(2 + 3 + 4 + 5 + 6 + 7):  # 每连续成功“当前上限”次加 1
            gate.succeeded()
        self.assertEqual(gate.concurrency, 8)
        gate.succeeded()
        self.assertEqual(gate.concurrency, 8)  # 不超过配置值

    def test_reduced_limit_caps_in_flight_requests(self):
        import threading
        gate = throttle.Limiter("x", throttle.Policy(rate=1e6, burst=1e6, concurrency=4, max_wait=5))
        gate.limited()
        active, peak, lock = [0], [0], threading.Lock()

        def work():
            with gate.slot():
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                time.sleep(0.02)
                with lock:
                    active[0] -= 1

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak[0], 2)

    def test_call_reports_limits_to_limiter(self):
        send = Mock(side_effect=[response(429, {"Retry-After": "0"}), response(200)])
        throttle.call("tmdb", send)
        self.assertEqual(throttle.limiter("tmdb").concurrency, throttle.limiter("tmdb").policy.concurrency // 2)


class AlistTest(unittest.TestCase):
    def test_driver_throttling_message_is_retried(self):
        busy = response(payload={"code": 500, "message": "failed get link: 请求太频繁，请稍后再试", "data": None})
        ok = response(payload={"code": 200, "data": {"sign": "abc"}})
        session = Mock()
        session.post.side_effect = [busy, ok]
        driver = AListDrive({"origin": "http://alist.invalid", "username": "u", "password": "p"},
                            lambda config: None, session)
        driver._token, driver._token_at = "token", time.monotonic()
        link = driver.link(Entry("A.mkv", False, "/Movies/A.mkv"))
        self.assertEqual(session.post.call_count, 2)
        self.assertTrue(link.url.endswith("/d/Movies/A.mkv?sign=abc"))


class RecognitionQuotaTest(unittest.TestCase):
    def test_ai_quota_falls_back_to_rules_and_retries_later(self):
        record = {"name": "某部冷门剧 2019", "path": "/share/x", "files": [{"path": "/share/x/01.mkv", "name": "01.mkv"}]}
        with patch.object(llm, "is_configured", return_value=True), \
             patch.object(recognition.tmdb, "search_media", return_value={"results": []}), \
             patch.object(recognition.store, "find_alias", return_value=None), \
             patch.object(recognition, "_ask_ai", side_effect=throttle.RateLimitError("llm", 3600)):
            result = recognition.recognize(None, record, titles=[])
            self.assertTrue(result["ai_deferred"])
            self.assertNotEqual(recognition.signature(record, ai=False), recognition.signature(record))


if __name__ == "__main__":
    unittest.main()
