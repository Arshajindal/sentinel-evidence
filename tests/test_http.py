"""Rate limiting and retry policy (§9.3). No network: httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from sentinel.ingest.http import RateLimitedHTTP, RetryableHTTPError, TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_token_bucket_spaces_requests_at_the_rate():
    clock = FakeClock()
    bucket = TokenBucket(rate=4, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        bucket.acquire()
    # First request uses the initial token; each later one waits 1/rate.
    assert clock.sleeps == pytest.approx([0.25] * 4)


def test_token_bucket_refills_while_idle():
    clock = FakeClock()
    bucket = TokenBucket(rate=2, clock=clock, sleep=clock.sleep)
    bucket.acquire()
    clock.now += 10
    bucket.acquire()
    assert clock.sleeps == []


def _http(handler, clock: FakeClock, max_retries: int = 3) -> RateLimitedHTTP:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RateLimitedHTTP(
        client,
        source="test",
        rate_per_s=1000,
        max_retries=max_retries,
        backoff_base_s=1.0,
        sleep=clock.sleep,
        clock=clock,
    )


def _sequence(*statuses: int, headers: dict | None = None):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses[min(len(calls), len(statuses) - 1)]
        calls.append(status)
        return httpx.Response(status, headers=headers or {}, json={"ok": status == 200})

    return handler, calls


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_retries_429_and_5xx_then_succeeds(status):
    clock = FakeClock()
    handler, calls = _sequence(status, status, 200)
    response = _http(handler, clock).get("https://example.test/x")
    assert response.status_code == 200
    assert calls == [status, status, 200]
    assert [s for s in clock.sleeps if s >= 1] == [1.0, 2.0]  # exponential backoff


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_does_not_retry_other_statuses(status):
    clock = FakeClock()
    handler, calls = _sequence(status)
    response = _http(handler, clock).get("https://example.test/x")
    assert response.status_code == status
    assert calls == [status]


def test_gives_up_after_max_retries():
    clock = FakeClock()
    handler, calls = _sequence(503)
    with pytest.raises(RetryableHTTPError):
        _http(handler, clock, max_retries=2).get("https://example.test/x")
    assert len(calls) == 3


def test_retry_after_header_sets_minimum_wait():
    clock = FakeClock()
    handler, _ = _sequence(429, 200, headers={"Retry-After": "7"})
    _http(handler, clock).get("https://example.test/x")
    assert 7.0 in clock.sleeps
