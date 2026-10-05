import contextlib
import io
import os
import pathlib
import socket
import threading
import time
import unittest
import urllib.error
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
        if self.path == "/garbage":  # not an HTTP status line
            self.wfile.write(b"garbage\r\n\r\n")
            return
        if self.path == "/chunked-cut":  # one 50 000 B chunk, then the connection closes
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"%x\r\n%s\r\n" % (BODY_SIZE // 2, b"x" * (BODY_SIZE // 2)))
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

    def test_ctrl_c_prints_the_report_for_what_was_downloaded(self):
        # 1 MB in 1 s, 1 MB in 3 s, then Ctrl+C 2 s into the third request, which got 0.5 MB
        # in its first 0.5 s and then stalled: 2.50 MB in 6 s -> 0.42 MB/s = 3.33 Mbit/s;
        # the two finished requests average 2.000 s
        finished = iter([(1_000_000, 1.0), (1_000_000, 3.0)])

        def fetch(url, progress):
            result = next(finished, None)
            if result:
                return result
            progress(500_000, 0.5)
            raise KeyboardInterrupt

        out = io.StringIO()
        clock = [0.0, 1.0, 4.0, 6.0]  # main(): start of requests 1, 2, 3, then the Ctrl+C
        with mock.patch.object(speedtest, "fetch", fetch), mock.patch.object(speedtest.time, "perf_counter", side_effect=clock), \
                contextlib.redirect_stdout(out):
            code = speedtest.main(["http://example.invalid/image.jpg"])
        self.assertEqual(code, 130)
        self.assertIn("Downloaded: 2.50 MB, 2 of 10 requests finished", out.getvalue())
        self.assertIn("Average request time: 2.000 s", out.getvalue())
        self.assertIn("Speed: 0.42 MB/s (3.33 Mbit/s)", out.getvalue())

    def test_ctrl_c_inside_the_first_request(self):
        # 0.5 MB arrived, Ctrl+C 1.5 s after the request started: 0.33 MB/s = 2.67 Mbit/s,
        # no request finished, so no average request time
        def fetch(url, progress):
            progress(500_000, 0.5)
            raise KeyboardInterrupt

        out = io.StringIO()
        with mock.patch.object(speedtest, "fetch", fetch), mock.patch.object(speedtest.time, "perf_counter", side_effect=[10.0, 11.5]), \
                contextlib.redirect_stdout(out):
            code = speedtest.main(["http://example.invalid/image.jpg"])
        self.assertEqual(code, 130)
        self.assertIn("Downloaded: 0.50 MB, 0 of 10 requests finished", out.getvalue())
        self.assertNotIn("Average request time", out.getvalue())
        self.assertIn("Speed: 0.33 MB/s (2.67 Mbit/s)", out.getvalue())

    def test_ctrl_c_counts_a_request_stalled_before_its_first_byte(self):
        # 1 MB in 1 s, then the second request hangs without a byte; Ctrl+C 2.5 s into it:
        # 1 MB in 3.5 s -> 0.29 MB/s = 2.29 Mbit/s
        out = io.StringIO()
        with mock.patch.object(speedtest, "fetch", side_effect=[(1_000_000, 1.0), KeyboardInterrupt]), \
                mock.patch.object(speedtest.time, "perf_counter", side_effect=[0.0, 1.0, 3.5]), \
                contextlib.redirect_stdout(out):
            speedtest.main(["http://example.invalid/image.jpg"])
        self.assertIn("Downloaded: 1.00 MB, 1 of 10 requests finished", out.getvalue())
        self.assertIn("Average request time: 1.000 s", out.getvalue())
        self.assertIn("Speed: 0.29 MB/s (2.29 Mbit/s)", out.getvalue())

    def test_ctrl_c_between_requests(self):
        # the finished request reported progress like the real fetch() does; it must count once:
        # 1 MB in 1 s -> 1.00 MB/s
        finished = iter([(1_000_000, 1.0)])

        def fetch(url, progress):
            result = next(finished, None)
            if not result:
                raise KeyboardInterrupt
            progress(*result)
            return result

        out = io.StringIO()
        with mock.patch.object(speedtest, "fetch", fetch), contextlib.redirect_stdout(out):
            speedtest.main(["http://example.invalid/image.jpg"])
        self.assertIn("Downloaded: 1.00 MB, 1 of 10 requests finished\n", out.getvalue())
        self.assertIn("Speed: 1.00 MB/s (8.00 Mbit/s)", out.getvalue())

    def test_ctrl_c_before_any_data(self):
        out = io.StringIO()
        with mock.patch.object(speedtest, "fetch", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(out):
            code = speedtest.main(["http://example.invalid/image.jpg"])
        self.assertEqual(code, 130)
        self.assertIn("Interrupted before any data arrived", out.getvalue())

    def test_cli_downloads_the_readme_image_by_default(self):
        with mock.patch.object(speedtest, "fetch", return_value=(100_000, 0.5)) as fetch, \
                contextlib.redirect_stdout(io.StringIO()):
            speedtest.main([])
        self.assertEqual(fetch.call_args[0][0], "https://esahubble.org/media/archives/images/large/heic1501a.jpg")

    def test_cli_prints_running_average_after_each_request(self):
        # 1 MB in 1 s -> avg 1.00; then 1 MB in 3 s -> 2 MB in 4 s = avg 0.50
        # (that request alone ran at 0.33, the mean of the two speeds is 0.67)
        out = io.StringIO()
        results = [(1_000_000, 1.0), (1_000_000, 3.0)] + [(1_000_000, 1.0)] * 8
        with mock.patch.object(speedtest, "fetch", side_effect=results), contextlib.redirect_stdout(out):
            speedtest.main(["http://example.invalid/image.jpg"])
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "Downloading http://example.invalid/image.jpg, 10 requests")
        self.assertTrue(lines[1].endswith("avg 1.00 MB/s"), lines[1])
        self.assertTrue(lines[2].endswith("avg 0.50 MB/s"), lines[2])


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

    def test_fetch_reports_progress_up_to_the_full_body(self):
        sizes = []
        speedtest.fetch(self.base + "/ok", lambda size, seconds: sizes.append(size))
        self.assertEqual(sizes[-1], BODY_SIZE)

    def test_fetch_reports_progress_in_seconds_since_request_start(self):
        seen = []
        _, seconds = speedtest.fetch(self.base + "/slow", lambda size, s: seen.append(s))
        self.assertGreaterEqual(seen[-1], 2 * DELAY)
        self.assertLessEqual(seen[-1], seconds)

    def test_cli_reports_a_malformed_response(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = speedtest.main([self.base + "/garbage"])
        self.assertEqual(code, 1)
        self.assertTrue(err.getvalue().startswith("error: "), err.getvalue())

    def test_cli_draws_progress_on_a_terminal_only(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        for err, terminal in ((Terminal(), True), (io.StringIO(), False)):
            with self.subTest(terminal=terminal):
                out = io.StringIO()
                with mock.patch.object(speedtest, "REDRAW", 0), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    speedtest.main([self.base + "/ok"])
                self.assertEqual("MB/s" in err.getvalue(), terminal)  # the progress line itself
                self.assertNotIn("\r", out.getvalue())

    def test_fetch_does_not_send_the_default_user_agent(self):
        # esahubble.org (the README example) answers 403 to Python-urllib/3.x
        speedtest.fetch(self.base + "/ok")
        self.assertFalse(Handler.user_agent.startswith("Python-urllib"), Handler.user_agent)

    def test_fetch_times_from_request_start_to_last_byte(self):
        # /slow waits DELAY before the headers and DELAY before the body
        _, seconds = speedtest.fetch(self.base + "/slow")
        self.assertGreaterEqual(seconds, 2 * DELAY)

    def test_fetch_gives_up_on_a_silent_server(self):
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen()  # the OS accepts the connection, nobody ever answers
        self.addCleanup(silent.close)
        socket.setdefaulttimeout(2)  # without fetch()'s own timeout: a failure below, not a hang
        self.addCleanup(socket.setdefaulttimeout, None)
        start = time.perf_counter()
        with mock.patch.object(speedtest, "TIMEOUT", 0.2), self.assertRaises(OSError):
            speedtest.fetch(f"http://127.0.0.1:{silent.getsockname()[1]}/")
        self.assertLess(time.perf_counter() - start, 1)

    def test_fetch_follows_redirects(self):
        size, _ = speedtest.fetch(self.base + "/redirect")
        self.assertEqual(size, BODY_SIZE)

    def test_fetch_without_content_length_counts_bytes_read(self):
        size, _ = speedtest.fetch(self.base + "/no-length")
        self.assertEqual(size, BODY_SIZE)

    def test_fetch_truncated_body_raises(self):
        with self.assertRaises(urllib.error.ContentTooShortError):
            speedtest.fetch(self.base + "/truncated")

    def test_fetch_cut_chunked_body_raises(self):
        # no byte count in the message: http.client drops the bytes of a chunk cut midway
        with self.assertRaises(urllib.error.ContentTooShortError) as cm:
            speedtest.fetch(self.base + "/chunked-cut")
        self.assertIn("in the middle of a chunked body", str(cm.exception))

    def test_fetch_accepts_https(self):
        # passes the scheme check and fails only on connecting to a closed local port
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        with self.assertRaises(urllib.error.URLError):
            speedtest.fetch(f"https://127.0.0.1:{port}/")

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
