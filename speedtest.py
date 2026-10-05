"""Measure download speed with 10 sequential requests to one URL."""

from __future__ import annotations  # `X | None` annotations on Python 3.9

import argparse
import http.client
import sys
import time
import urllib.parse
import urllib.request

COUNT = 10
DEFAULT_URL = "https://esahubble.org/media/archives/images/large/heic1501a.jpg"  # Hubble, 26.8 MB
MB = 10**6
TIMEOUT = 30  # seconds per socket operation, not for the whole download
# esahubble.org (the README example) and Wikimedia answer 403 to the default Python-urllib agent
USER_AGENT = "speedtest.py/1.0"


def fetch(url: str) -> tuple[int, float]:
    """Download url once; return (body bytes, seconds from request start to the last byte)."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        # Read the whole body: on a body shorter than Content-Length read() raises
        # IncompleteRead, while a read(n) loop would silently stop early.
        # The body is held in memory: fine for images, not for multi-GB files.
        size = len(response.read())
    return size, time.perf_counter() - start


def summarize(results: list[tuple[int, float]]) -> tuple[int, float, float]:
    """Return (total bytes, average seconds per request, speed in MB/s)."""
    total_bytes = sum(size for size, _ in results)
    total_seconds = sum(seconds for _, seconds in results)
    return total_bytes, total_seconds / len(results), total_bytes / MB / total_seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default=DEFAULT_URL,
                        help="file to download, 20+ MB (default: a 26.8 MB Hubble image)")
    args = parser.parse_args(argv)

    print(f"Downloading {args.url}, {COUNT} requests", flush=True)
    results = []
    try:
        for i in range(1, COUNT + 1):
            size, seconds = fetch(args.url)
            results.append((size, seconds))
            avg = summarize(results)[2]
            print(f"{i:2}/{COUNT}  {size / MB:8.2f} MB  {seconds:7.3f} s  avg {avg:.2f} MB/s", flush=True)
    # OSError: HTTP status, DNS, connection, timeout; HTTPException: truncated body;
    # ValueError: malformed URL
    except (OSError, http.client.HTTPException, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    total_bytes, avg_seconds, mb_per_s = summarize(results)
    print(f"Downloaded: {total_bytes / MB:.2f} MB in {COUNT} requests")
    print(f"Average request time: {avg_seconds:.3f} s")
    print(f"Speed: {mb_per_s:.2f} MB/s ({mb_per_s * 8:.2f} Mbit/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
