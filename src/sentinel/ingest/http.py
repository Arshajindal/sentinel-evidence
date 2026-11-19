"""HTTP plumbing shared by every source (sentinel-project.md §9.3).

Rate limiting is a token bucket per source. Retries use exponential backoff
through tenacity, on 429 and 5xx responses and on transient transport errors
(timeouts, resets, a server closing the connection) only (ADR-042); a
Retry-After header, when present, sets the minimum wait. Other 4xx responses
and errors in the request itself are never retried.
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


# Transient failures of the connection, not of the request (ADR-042).
# UnsupportedProtocol, LocalProtocolError and ProxyError are not here: a retry
# would send the same broken request again.
TRANSIENT_TRANSPORT_ERRORS: tuple[type[httpx.TransportError], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


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
    """GET or POST with a token bucket in front and retries on 429/5xx and
    dropped connections behind."""

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
        return self._send("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        """A POST is retried like a GET, so use it only where repeating the
        request is harmless, such as a language-model call with no side effects."""
        return self._send("POST", url, **kwargs)

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        retrying = Retrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=self._wait,
            retry=retry_if_exception_type((RetryableHTTPError, *TRANSIENT_TRANSPORT_ERRORS)),
            before_sleep=self._log_retry,
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                self.bucket.acquire()
                response = self.client.request(method, url, **kwargs)
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
        error = None if isinstance(exc, RetryableHTTPError) or exc is None else type(exc).__name__
        log.warning(
            "http_retry",
            source=self.source,
            attempt=state.attempt_number,
            status=status,
            error=error,
            wait_s=state.next_action.sleep if state.next_action else None,
        )
