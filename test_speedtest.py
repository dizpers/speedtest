import contextlib
import http.client
import io
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import speedtest

BODY_SIZE = 100_000


class Handler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        Handler.hits += 1
        self.send_response(200)
        if self.path != "/no-length":
            self.send_header("Content-Length", str(BODY_SIZE))
        self.end_headers()
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


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def test_fetch_without_content_length_counts_bytes_read(self):
        size, _ = speedtest.fetch(self.base + "/no-length")
        self.assertEqual(size, BODY_SIZE)

    def test_fetch_truncated_body_raises(self):
        with self.assertRaises(http.client.IncompleteRead):
            speedtest.fetch(self.base + "/truncated")

    def test_cli_makes_ten_requests_and_prints_summary(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = speedtest.main([self.base + "/ok"])
        self.assertEqual(code, 0)
        self.assertEqual(Handler.hits, 10)
        self.assertIn("Downloaded: 1.00 MB in 10 requests", out.getvalue())  # 10 x 100 000 B
        self.assertIn("MB/s", out.getvalue())
