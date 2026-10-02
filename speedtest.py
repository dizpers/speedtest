"""Measure download speed with 10 sequential requests to one URL."""

import time
import urllib.request

MB = 10**6
TIMEOUT = 30  # seconds per socket operation, not for the whole download
USER_AGENT = "speedtest.py/1.0"


def fetch(url: str) -> tuple[int, float]:
    """Download url once; return (body bytes, seconds from request start to the last byte)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        size = len(response.read())
    return size, time.perf_counter() - start


def summarize(results: list[tuple[int, float]]) -> tuple[int, float, float]:
    """Return (total bytes, average seconds per request, speed in MB/s)."""
    total_bytes = sum(size for size, _ in results)
    total_seconds = sum(seconds for _, seconds in results)
    return total_bytes, total_seconds / len(results), total_bytes / MB / total_seconds
