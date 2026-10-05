import contextlib
import http.client
import io
import os
import pathlib
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import speedtest

BODY_SIZE = 100_000
DELAY = 0.1


class Handler(BaseHTTPRequestHandler):
    hits = 0
    user_agent = None

    def do_GET(self):
        Handler.hits += 1
        Handler.user_agent = self.headers.get("User-Agent")
        if self.path == "/missing":
            self.send_error(404)
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        slow = self.path == "/slow"
        if slow:
            time.sleep(DELAY)
        self.send_response(200)
        if self.path != "/no-length":
            self.send_header("Content-Length", str(BODY_SIZE))
        self.end_headers()
        if slow:
            time.sleep(DELAY)
        # HTTP/1.0 handler: the server closes the connection after the body
        self.wfile.write(b"x" * (BODY_SIZE // 2 if self.path == "/truncated" else BODY_SIZE))

    def log_message(self, format, *args):
        pass  # the server thread would otherwise write to the captured stderr


class SummaryTest(unittest.TestCase):
    def test_summary_divides_total_bytes_by_total_time(self):
        # 2 MB in 2 s -> 1.00 MB/s; averaging the per-request speeds (2.0 and 0.67) would give 1.33
        total_bytes, avg_seconds, mb_per_s = speedtest.summarize([(1_000_000, 0.5), (1_000_000, 1.5)])
        self.assertEqual(total_bytes, 2_000_000)
        self.assertAlmostEqual(avg_seconds, 1.0)
        self.assertAlmostEqual(mb_per_s, 1.0)

    def test_cli_prints_average_time_and_speed(self):
        # 10 x (100 000 B in 0.5 s): 1.00 MB in 5 s -> 0.500 s average, 0.20 MB/s = 1.60 Mbit/s
        out = io.StringIO()
        with mock.patch.object(speedtest, "fetch", return_value=(100_000, 0.5)), contextlib.redirect_stdout(out):
            code = speedtest.main(["http://example.invalid/image.jpg"])
        self.assertEqual(code, 0)
        self.assertIn("Average request time: 0.500 s", out.getvalue())
        self.assertIn("Speed: 0.20 MB/s (1.60 Mbit/s)", out.getvalue())


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # with http_proxy set, urllib would send even 127.0.0.1 requests to the proxy
        no_proxy = mock.patch.dict(os.environ, {"no_proxy": "*"})
        no_proxy.start()
        cls.addClassCleanup(no_proxy.stop)
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.hits = 0

    def test_fetch_counts_body_bytes(self):
        size, seconds = speedtest.fetch(self.base + "/ok")
        self.assertEqual(size, BODY_SIZE)
        self.assertGreater(seconds, 0)

    def test_fetch_does_not_send_the_default_user_agent(self):
        # esahubble.org (the README example) answers 403 to Python-urllib/3.x
        speedtest.fetch(self.base + "/ok")
        self.assertFalse(Handler.user_agent.startswith("Python-urllib"), Handler.user_agent)

    def test_fetch_times_from_request_start_to_last_byte(self):
        # /slow waits DELAY before the headers and DELAY before the body
        _, seconds = speedtest.fetch(self.base + "/slow")
        self.assertGreaterEqual(seconds, 2 * DELAY)

    def test_fetch_follows_redirects(self):
        size, _ = speedtest.fetch(self.base + "/redirect")
        self.assertEqual(size, BODY_SIZE)

    def test_fetch_without_content_length_counts_bytes_read(self):
        size, _ = speedtest.fetch(self.base + "/no-length")
        self.assertEqual(size, BODY_SIZE)

    def test_fetch_truncated_body_raises(self):
        with self.assertRaises(http.client.IncompleteRead):
            speedtest.fetch(self.base + "/truncated")

    def test_fetch_rejects_non_http_url(self):
        # urllib would happily read a local file and report it as download speed
        with self.assertRaises(ValueError):
            speedtest.fetch(pathlib.Path(__file__).resolve().as_uri())

    def test_cli_makes_ten_requests_and_prints_summary(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = speedtest.main([self.base + "/ok"])
        self.assertEqual(code, 0)
        self.assertEqual(Handler.hits, 10)
        self.assertIn("Downloaded: 1.00 MB in 10 requests", out.getvalue())  # 10 x 100 000 B
        self.assertIn("MB/s", out.getvalue())

    def test_cli_reports_errors_and_exits_1(self):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_url = f"http://127.0.0.1:{closed.getsockname()[1]}/"
        closed.close()
        for url in (self.base + "/missing", self.base + "/truncated", closed_url, "not-a-url"):
            with self.subTest(url=url):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = speedtest.main([url])
                self.assertEqual(code, 1)
                self.assertTrue(err.getvalue().startswith("error: "), err.getvalue())
                self.assertNotIn("Speed:", out.getvalue())
