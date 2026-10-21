import email.utils
import time
import unittest
from unittest.mock import Mock, patch

import requests

from connectors import alist, llm, throttle
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


class AlistTest(unittest.TestCase):
    def test_driver_throttling_message_is_retried(self):
        busy = response(payload={"code": 500, "message": "failed get link: 请求太频繁，请稍后再试", "data": None})
        ok = response(payload={"code": 200, "data": {"sign": "abc", "raw_url": "http://raw/x"}})
        with patch.object(alist.requests, "post", side_effect=[busy, ok]) as post:
            links = alist.get_file("/Movies/A.mkv", "token")
        self.assertEqual(post.call_count, 2)
        self.assertTrue(links["signed_url"].endswith("/d/Movies/A.mkv?sign=abc"))
        self.assertEqual(links["raw_url"], "http://raw/x")

    def test_token_is_cached_between_calls(self):
        with patch.dict("os.environ", {"ALIST_USERNAME": "u", "ALIST_PASSWORD": "p"}), \
             patch.object(alist, "get_token", return_value="tok") as login:
            self.assertEqual(alist.configured_token(), "tok")
            self.assertEqual(alist.configured_token(), "tok")
            alist.forget_token()
            alist.configured_token()
        self.assertEqual(login.call_count, 2)


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
