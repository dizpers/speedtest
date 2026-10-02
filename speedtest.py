"""Measure download speed with 10 sequential requests to one URL."""

MB = 10**6


def summarize(results: list[tuple[int, float]]) -> tuple[int, float, float]:
    """Return (total bytes, average seconds per request, speed in MB/s)."""
    total_bytes = sum(size for size, _ in results)
    total_seconds = sum(seconds for _, seconds in results)
    return total_bytes, total_seconds / len(results), total_bytes / MB / total_seconds
