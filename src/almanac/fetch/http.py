from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Self
from urllib.parse import urljoin, urlsplit

import httpx

from almanac import __version__
from almanac.errors import FetchBlocked, FetchFailed
from almanac.fetch.fetchlog import FetchLog
from almanac.fetch.ratelimit import RateLimiter
from almanac.fetch.robots import RobotsCache
from almanac.fetch.safety import check_url, default_resolve
from almanac.models import Settings

RETRIES = 3
MAX_BACKOFF = 60.0
_REDIRECTS = (301, 302, 303, 307, 308)


@dataclass
class HttpResult:
    status: int
    final_url: str
    body: bytes
    content_type: str | None
    etag: str | None
    last_modified: str | None


def _retry_after(value: str | None, now: datetime) -> float:
    if not value:
        return 0.0
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, (parsedate_to_datetime(value) - now).total_seconds())
    except (TypeError, ValueError):
        return 0.0


class HttpClient:
    def __init__(
        self,
        settings: Settings,
        log: FetchLog,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        resolve: Callable[[str], list[str]] = default_resolve,
    ):
        self._settings = settings
        self._log = log
        self._clock = clock
        self._sleep = sleep
        self._resolve = resolve
        self.user_agent = f"Almanac/{__version__}" + (f" (+{settings.contact})" if settings.contact else "")
        self._http = httpx.Client(transport=transport, follow_redirects=False, timeout=30)
        self._limiter = RateLimiter(settings.rate_limit_per_host_rps, clock, sleep)
        self._robots = RobotsCache(self._fetch_robots, self.user_agent)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, url: str, *, etag: str | None = None, last_modified: str | None = None) -> HttpResult:
        started = self._clock()
        headers = {"User-Agent": self.user_agent, "Accept": "text/html, text/markdown, text/plain, */*;q=0.5"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        def ms() -> int:
            return int((self._clock() - started) * 1000)

        try:
            result = self._fetch_url(url, headers, check_robots=True, retries=RETRIES)
        except (FetchBlocked, FetchFailed) as e:
            self._log.record(url, getattr(e, "status", None), 0, ms(), e.reason)
            raise
        self._log.record(url, result.status, len(result.body), ms(), None)
        return result

    def _fetch_robots(self, url: str) -> tuple[int, str]:
        result = self._fetch_url(url, {"User-Agent": self.user_agent}, check_robots=False, retries=0, raw=True)
        return result.status, result.body.decode("utf-8", errors="replace")

    def _fetch_url(
        self, url: str, headers: dict[str, str], *, check_robots: bool, retries: int, raw: bool = False
    ) -> HttpResult:
        """Follow redirects manually, validating scheme, address and robots on every hop."""
        current = url
        for hops in range(self._settings.max_redirects + 1):
            check_url(current, self._settings.allow_private_network, self._resolve)
            if check_robots and not self._robots.allowed(current):
                raise FetchBlocked("robots")
            if check_robots:
                host = urlsplit(current).netloc
                delay = self._robots.crawl_delay(host)
                if delay:
                    self._limiter.set_min_interval(host, delay)
            result = self._request(current, headers, retries, raw)
            if result.status not in _REDIRECTS:
                return result
            location = result.location
            if not location:
                raise FetchFailed(f"http-{result.status}", result.status)
            if hops >= self._settings.max_redirects:
                raise FetchFailed("too-many-redirects")
            current = urljoin(current, location)
        raise FetchFailed("too-many-redirects")  # pragma: no cover

    def _request(self, url: str, headers: dict[str, str], retries: int, raw: bool) -> _Result:
        host = urlsplit(url).netloc
        for attempt in range(retries + 1):
            self._limiter.wait(host)
            retry_after = 0.0
            try:
                result = self._send(url, headers, raw)
            except httpx.InvalidURL as e:
                raise FetchFailed("network: invalid url") from e
            except httpx.HTTPError as e:
                if attempt >= retries:
                    raise FetchFailed(f"network: {e}") from e
            else:
                if raw:
                    return result
                if result.status == 429 or result.status >= 500:
                    if attempt >= retries:
                        raise FetchFailed(f"http-{result.status}", result.status)
                    retry_after = _retry_after(result.retry_after, datetime.now(UTC))
                elif result.status in _REDIRECTS or result.status in (200, 304):
                    return result
                else:
                    raise FetchFailed(f"http-{result.status}", result.status)
            self._sleep(min(MAX_BACKOFF, max(retry_after, float(2**attempt))))
        raise FetchFailed("network: retries exhausted")  # pragma: no cover

    def _send(self, url: str, headers: dict[str, str], raw: bool) -> _Result:
        with self._http.stream("GET", url, headers=headers) as resp:
            status = resp.status_code
            h = resp.headers
            result = _Result(
                status=status,
                final_url=url,
                body=b"",
                content_type=h.get("content-type"),
                etag=h.get("etag"),
                last_modified=h.get("last-modified"),
                location=h.get("location"),
                retry_after=h.get("retry-after"),
            )
            if status in _REDIRECTS or status == 304 or status == 429 or status >= 500:
                return result
            if not raw and status == 200 and _noai(h.get_list("x-robots-tag")):
                raise FetchBlocked("noai")
            limit = self._settings.max_page_bytes
            declared = h.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise FetchFailed("too-large", status)
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise FetchFailed("too-large", status)
                chunks.append(chunk)
            result.body = b"".join(chunks)
            return result


@dataclass
class _Result(HttpResult):
    location: str | None = None
    retry_after: str | None = None


def _noai(values: list[str]) -> bool:
    for value in values:
        for token in value.lower().replace(";", ",").split(","):
            if token.split(":")[-1].strip() == "noai":
                return True
    return False
