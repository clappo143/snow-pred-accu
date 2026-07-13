"""common.get retry policy: transient failures (connect/read timeouts,
HTTP 429, and 5xx) are retried with backoff; genuine 4xx client errors
fail fast. Regression for the 2026-07-13 run where a back-to-back 429
from api.open-meteo.com failed three resorts' openmeteo collector and
exited the whole run non-zero."""
import unittest
from unittest import mock

import requests

from collectors import common


def _resp(status: int, headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.headers = headers or {}
    r.url = "http://x"
    return r


class RetryPolicy(unittest.TestCase):
    def test_429_then_success(self):
        with mock.patch("requests.get",
                        side_effect=[_resp(429, {"Retry-After": "0"}), _resp(200)]), \
                mock.patch("time.sleep") as slept:
            self.assertEqual(common.get("http://x").status_code, 200)
            self.assertTrue(slept.called)

    def test_5xx_then_success(self):
        with mock.patch("requests.get", side_effect=[_resp(503), _resp(200)]), \
                mock.patch("time.sleep"):
            self.assertEqual(common.get("http://x").status_code, 200)

    def test_persistent_429_raises(self):
        with mock.patch("requests.get", side_effect=[_resp(429)] * 3), \
                mock.patch("time.sleep"):
            with self.assertRaises(requests.exceptions.HTTPError) as cm:
                common.get("http://x")
            self.assertEqual(cm.exception.response.status_code, 429)

    def test_4xx_fails_fast(self):
        calls = mock.Mock(side_effect=lambda *a, **k: _resp(404))
        with mock.patch("requests.get", calls), mock.patch("time.sleep") as slept:
            with self.assertRaises(requests.exceptions.HTTPError):
                common.get("http://x")
            self.assertEqual(calls.call_count, 1)   # no retry
            self.assertFalse(slept.called)

    def test_retry_after_header_honoured(self):
        with mock.patch("requests.get",
                        side_effect=[_resp(429, {"Retry-After": "7"}), _resp(200)]), \
                mock.patch("time.sleep") as slept:
            common.get("http://x")
            slept.assert_called_once_with(7.0)


if __name__ == "__main__":
    unittest.main()
