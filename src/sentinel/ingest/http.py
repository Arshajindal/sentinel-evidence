"""HTTP plumbing shared by every source (sentinel-project.md §9.3).

Rate limiting is a token bucket per source. Retries use exponential backoff
through tenacity, on 429 and 5xx responses only; a Retry-After header, when
present, sets the minimum wait.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

from sentinel.config import Settings
from sentinel.log import get_logger

log = get_logger(__name__)


class TokenBucket:
    """Allows `rate` requests per second, with bursts up to `capacity`."""

    def __init__(
        self,
        rate: float,
        capacity: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                self._sleep(wait)
                self._last = self._clock()
                self._tokens = 0.0
            else:
                self._tokens -= 1


class RetryableHTTPError(Exception):
    """A 429 or 5xx response. Raised so tenacity retries it."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code} from {response.request.url}")
        self.response = response


def build_http_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        timeout=settings.http_timeout_s,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent, "From": settings.contact_email},
        limits=httpx.Limits(max_connections=settings.http_max_connections),
    )


class RateLimitedHTTP:
    """GET with a token bucket in front and retries on 429/5xx behind."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        source: str,
        rate_per_s: float,
        max_retries: int,
        backoff_base_s: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.source = source
        self.bucket = TokenBucket(rate_per_s, clock=clock, sleep=sleep)
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep

    def get(self, url: str, **kwargs) -> httpx.Response:
        retrying = Retrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=self._wait,
            retry=retry_if_exception_type(RetryableHTTPError),
            before_sleep=self._log_retry,
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                self.bucket.acquire()
                response = self.client.get(url, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableHTTPError(response)
                return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, state: RetryCallState) -> float:
        backoff = self.backoff_base_s * 2 ** (state.attempt_number - 1)
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, RetryableHTTPError):
            retry_after = exc.response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return max(backoff, float(retry_after))
        return backoff

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        status = exc.response.status_code if isinstance(exc, RetryableHTTPError) else None
        log.warning(
            "http_retry",
            source=self.source,
            attempt=state.attempt_number,
            status=status,
            wait_s=state.next_action.sleep if state.next_action else None,
        )
