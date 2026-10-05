"""Measure download speed with 10 sequential requests to one URL."""

from __future__ import annotations  # `X | None` annotations on Python 3.9

import argparse
import http.client
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

COUNT = 10
DEFAULT_URL = "https://esahubble.org/media/archives/images/large/heic1501a.jpg"  # Hubble, 26.8 MB
MB = 10**6
REDRAW = 0.2  # seconds between progress redraws; drawing runs inside the timed download
TIMEOUT = 30  # seconds per socket operation, not for the whole download
# esahubble.org (the README example) and Wikimedia answer 403 to the default Python-urllib agent
USER_AGENT = "speedtest.py/1.0"


def fetch(url: str, progress: Callable[[int, float], None] | None = None) -> tuple[int, float]:
    """Download url once; return (body bytes, seconds from request start to the last byte).

    progress, if given, is called with (bytes so far, seconds so far) after every chunk.
    """
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        expected = response.headers.get("Content-Length")
        size = 0
        while chunk := response.read(64 * 1024):
            size += len(chunk)
            if progress:
                progress(size, time.perf_counter() - start)
    seconds = time.perf_counter() - start
    # read(n) returns b"" at a premature EOF instead of raising, so check the length here
    # (the same check and error as urllib.request.urlretrieve)
    if expected is not None and size < int(expected):
        raise urllib.error.ContentTooShortError(
            f"retrieval incomplete: got only {size} out of {expected} bytes", None)
    return size, seconds


def summarize(results: list[tuple[int, float]]) -> tuple[int, float, float]:
    """Return (total bytes, average seconds per request, speed in MB/s)."""
    total_bytes = sum(size for size, _ in results)
    total_seconds = sum(seconds for _, seconds in results)
    return total_bytes, total_seconds / len(results), total_bytes / MB / total_seconds


def erase_progress_line() -> None:
    if sys.stderr.isatty():
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)


def print_report(results: list[tuple[int, float]], partial: tuple[int, float] | None = None) -> None:
    """Print the summary; after Ctrl+C, partial is the request in flight, counted in MB and speed."""
    total_bytes, _, mb_per_s = summarize((results + [partial]) if partial else results)
    requests = f"{len(results)} request" + ("" if len(results) == 1 else "s")
    tail = " and part of the next" if partial else ""
    print(f"Downloaded: {total_bytes / MB:.2f} MB in {requests}{tail}")
    if results:
        print(f"Average request time: {summarize(results)[1]:.3f} s")
    print(f"Speed: {mb_per_s:.2f} MB/s ({mb_per_s * 8:.2f} Mbit/s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default=DEFAULT_URL,
                        help="file to download, 20+ MB (default: a 26.8 MB Hubble image)")
    args = parser.parse_args(argv)

    print(f"Downloading {args.url}, {COUNT} requests", flush=True)
    results: list[tuple[int, float]] = []
    partial: tuple[int, float] | None = None  # (bytes, seconds) of the request in flight
    drawn_at = 0.0

    def progress(size: int, seconds: float) -> None:
        nonlocal partial, drawn_at
        partial = (size, seconds)
        # live line only on a terminal: no \r garbage in logs, pipes and CI
        if sys.stderr.isatty() and seconds - drawn_at >= REDRAW:
            drawn_at = seconds
            avg = summarize(results + [partial])[2]
            print(f"\r{len(results) + 1:2}/{COUNT}  {size / MB:8.2f} MB  avg {avg:.2f} MB/s",
                  end="", file=sys.stderr, flush=True)

    try:
        for i in range(1, COUNT + 1):
            drawn_at = 0.0
            size, seconds = fetch(args.url, progress)
            erase_progress_line()
            results.append((size, seconds))
            partial = None
            avg = summarize(results)[2]
            print(f"{i:2}/{COUNT}  {size / MB:8.2f} MB  {seconds:7.3f} s  avg {avg:.2f} MB/s", flush=True)
    except KeyboardInterrupt:
        erase_progress_line()
        if not results and not partial:
            print("Interrupted before any data arrived")
            return 130
        print("Interrupted")
        print_report(results, partial)
        return 130
    # OSError: HTTP status, DNS, connection, timeout, body shorter than Content-Length;
    # HTTPException: malformed response, e.g. a cut chunked body; ValueError: malformed URL
    except (OSError, http.client.HTTPException, ValueError) as e:
        erase_progress_line()
        print(f"error: {e}", file=sys.stderr)
        return 1

    print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
