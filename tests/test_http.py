from __future__ import annotations

import httpx
import pytest

from almanac import __version__
from almanac.db import connect
from almanac.errors import FetchBlocked, FetchFailed
from almanac.fetch.fetchlog import FetchLog
from almanac.fetch.http import HttpClient
from almanac.fetch.ratelimit import RateLimiter
from almanac.fetch.robots import RobotsCache
from almanac.fetch.safety import check_url
from almanac.models import Settings

PUBLIC = "93.184.216.34"


def pub(host: str) -> list[str]:
    return [PUBLIC]


def real(host: str) -> list[str]:
    return ["127.0.0.1"]


@pytest.fixture
def client_factory(tmp_path):
    """client_factory(robots=..., handler=..., **settings) -> (client, log_rows).

    The fake clock advances by `latency` per network request; sleeping advances it too.
    `client.sleeps` and `client.requests` record backoff/limiter sleeps and requests seen.
    """

    def factory(robots=(200, ""), handler=None, latency=1.0, resolve=pub, **settings):
        conn = connect(tmp_path / "t.db")
        t = [0.0]
        sleeps: list[float] = []
        requests: list[httpx.Request] = []

        def sleep(s: float) -> None:
            sleeps.append(s)
            t[0] += s

        def route(request: httpx.Request) -> httpx.Response:
            t[0] += latency
            requests.append(request)
            if request.url.path == "/robots.txt":
                if isinstance(robots, Exception):
                    raise robots
                status, text = robots if isinstance(robots, tuple) else (200, robots)
                return httpx.Response(status, text=text)
            if handler is None:
                return httpx.Response(200, text="hello", headers={"content-type": "text/html", "etag": '"v1"'})
            return handler(request)

        client = HttpClient(
            Settings(**settings),
            FetchLog(conn, "run1"),
            transport=httpx.MockTransport(route),
            clock=lambda: t[0],
            sleep=sleep,
            resolve=resolve,
        )
        client.sleeps = sleeps
        client.requests = requests

        def log_rows():
            return [dict(r) for r in conn.execute("select * from fetch_log order by id")]

        return client, log_rows

    return factory


def page_requests(client):
    return [r for r in client.requests if r.url.path != "/robots.txt"]


def test_rate_limiter_per_host():
    sleeps = []
    t = [0.0]
    rl = RateLimiter(1, clock=lambda: t[0], sleep=sleeps.append)
    rl.wait("a")
    rl.wait("a")
    rl.wait("b")
    assert sleeps == [pytest.approx(1.0)]


def test_rate_limiter_min_interval():
    sleeps = []
    rl = RateLimiter(1, clock=lambda: 0.0, sleep=sleeps.append)
    rl.set_min_interval("a", 3)
    rl.wait("a")
    rl.wait("a")
    assert sleeps == [pytest.approx(3.0)]


def test_robots_cache_semantics():
    calls = []

    def fetch(url):
        calls.append(url)
        return {"https://a.test/robots.txt": (200, "User-agent: *\nDisallow: /x\nCrawl-delay: 2")}.get(url, (404, ""))

    rc = RobotsCache(fetch, "Almanac/0.1.0")
    assert not rc.allowed("https://a.test/x/y")
    assert rc.allowed("https://a.test/z")
    assert rc.allowed("https://b.test/x")
    assert rc.crawl_delay("a.test") == 2
    assert rc.crawl_delay("b.test") is None
    assert len(calls) == 2  # cached per host


def test_robots_network_error_disallows():
    def fetch(url):
        raise OSError("boom")

    assert not RobotsCache(fetch, "Almanac").allowed("https://a.test/")


def test_robots_disallow_blocks_and_logs(client_factory):
    c, log_rows = client_factory(robots="User-agent: *\nDisallow: /private")
    with pytest.raises(FetchBlocked, match="robots"):
        c.get("https://docs.test/private/x")
    row = log_rows()[-1]
    assert row["error"] == "robots"
    assert row["status"] is None
    assert page_requests(c) == []
    assert len(log_rows()) == 1  # robots.txt fetch itself is not logged


def test_robots_allowed_path_ok(client_factory):
    c, log_rows = client_factory(robots="User-agent: *\nDisallow: /private")
    r = c.get("https://docs.test/public")
    assert r.status == 200 and r.body == b"hello"
    assert r.etag == '"v1"' and r.content_type == "text/html"
    row = log_rows()[-1]
    assert row["status"] == 200 and row["bytes"] == 5 and row["error"] is None and row["run_id"] == "run1"


def test_robots_5xx_disallows_all(client_factory):
    c, log_rows = client_factory(robots=(503, ""))
    with pytest.raises(FetchBlocked, match="robots"):
        c.get("https://docs.test/anything")
    assert log_rows()[-1]["error"] == "robots"


def test_robots_unreachable_disallows_all(client_factory):
    c, _ = client_factory(robots=httpx.ConnectError("down"))
    with pytest.raises(FetchBlocked, match="robots"):
        c.get("https://docs.test/anything")


def test_robots_404_allows_all(client_factory):
    c, _ = client_factory(robots=(404, "not found"))
    assert c.get("https://docs.test/anything").status == 200


def test_robots_fetched_once_per_host(client_factory):
    c, _ = client_factory()
    c.get("https://docs.test/a")
    c.get("https://docs.test/b")
    assert [r.url.path for r in c.requests].count("/robots.txt") == 1


def test_crawl_delay_raises_interval(client_factory):
    c, _ = client_factory(robots="User-agent: *\nCrawl-delay: 3", latency=0)
    c.get("https://docs.test/a")
    c.get("https://docs.test/b")
    # robots.txt fetch used the default 1s spacing; from then on the 3s Crawl-delay applies
    assert c.sleeps == [pytest.approx(1.0), pytest.approx(3.0)]


def test_rate_limit_applies_between_requests(client_factory):
    c, _ = client_factory(latency=0)
    c.get("https://docs.test/a")
    c.get("https://docs.test/b")
    assert c.sleeps and all(s == pytest.approx(1.0) for s in c.sleeps)


def test_429_honours_retry_after(client_factory):
    n = []

    def handler(request):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, text="ok")

    c, log_rows = client_factory(handler=handler)
    assert c.get("https://docs.test/a").status == 200
    assert c.sleeps == [2]
    assert len(log_rows()) == 1


def test_retry_after_http_date_and_cap(client_factory):
    n = []

    def handler(request):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(429, headers={"retry-after": "9999"})
        return httpx.Response(200, text="ok")

    c, _ = client_factory(handler=handler)
    c.get("https://docs.test/a")
    assert c.sleeps == [60]

    n.clear()

    def handler2(request):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(503, headers={"retry-after": "Thu, 01 Jan 1970 00:00:00 GMT"})
        return httpx.Response(200, text="ok")

    c, _ = client_factory(handler=handler2)
    c.get("https://docs.test/a")
    assert c.sleeps == [1]  # past date -> falls back to exponential backoff


def test_5xx_retried_three_times_then_fails(client_factory):
    c, log_rows = client_factory(handler=lambda r: httpx.Response(503))
    with pytest.raises(FetchFailed, match="http-503"):
        c.get("https://docs.test/a")
    assert len(page_requests(c)) == 4
    assert c.sleeps == [1, 2, 4]
    assert log_rows()[-1]["error"] == "http-503"
    assert log_rows()[-1]["status"] == 503
    assert len(log_rows()) == 1


def test_4xx_not_retried(client_factory):
    c, log_rows = client_factory(handler=lambda r: httpx.Response(404))
    with pytest.raises(FetchFailed, match="http-404"):
        c.get("https://docs.test/a")
    assert len(page_requests(c)) == 1
    assert log_rows()[-1]["status"] == 404


def test_network_error_retried_then_fails(client_factory):
    def handler(request):
        raise httpx.ConnectError("refused")

    c, log_rows = client_factory(handler=handler)
    with pytest.raises(FetchFailed) as ei:
        c.get("https://docs.test/a")
    assert ei.value.reason.startswith("network: ")
    assert len(page_requests(c)) == 4
    assert log_rows()[-1]["error"].startswith("network: ")


def test_conditional_headers_and_304(client_factory):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(304)

    c, log_rows = client_factory(handler=handler)
    r = c.get("https://docs.test/a", etag='"v1"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    assert r.status == 304 and r.body == b""
    assert seen["if-none-match"] == '"v1"'
    assert seen["if-modified-since"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert log_rows()[-1]["status"] == 304


def test_too_large(client_factory):
    c, log_rows = client_factory(handler=lambda r: httpx.Response(200, content=b"x" * 11), max_page_bytes=10)
    with pytest.raises(FetchFailed, match="too-large"):
        c.get("https://docs.test/a")
    assert log_rows()[-1]["error"] == "too-large"
    assert log_rows()[-1]["status"] == 200
    ok, _ = client_factory(handler=lambda r: httpx.Response(200, content=b"x" * 10), max_page_bytes=10)
    assert len(ok.get("https://docs.test/a").body) == 10


def redirect_chain(n):
    def handler(request):
        i = int(request.url.path.removeprefix("/hop") or 0)
        if i < n:
            return httpx.Response(302, headers={"location": f"/hop{i + 1}"})
        return httpx.Response(200, text="end")

    return handler


def test_redirects_capped_and_final_url(client_factory):
    c, _ = client_factory(handler=redirect_chain(5))
    r = c.get("https://docs.test/hop0")
    assert r.final_url == "https://docs.test/hop5"
    c2, log_rows = client_factory(handler=redirect_chain(6))
    with pytest.raises(FetchFailed, match="too-many-redirects"):
        c2.get("https://docs.test/hop0")
    assert log_rows()[-1]["error"] == "too-many-redirects"


def test_redirect_to_private_blocked(client_factory):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    c, log_rows = client_factory(handler=handler)
    with pytest.raises(FetchBlocked, match="private-address"):
        c.get("https://docs.test/a")
    assert log_rows()[-1]["error"] == "private-address"
    assert all(r.url.host != "169.254.169.254" for r in c.requests)


def test_redirect_to_other_scheme_blocked(client_factory):
    c, _ = client_factory(handler=lambda r: httpx.Response(302, headers={"location": "ftp://docs.test/x"}))
    with pytest.raises(FetchBlocked, match="scheme"):
        c.get("https://docs.test/a")


def test_redirect_without_location_is_failure(client_factory):
    c, _ = client_factory(handler=lambda r: httpx.Response(302))
    with pytest.raises(FetchFailed, match="http-302"):
        c.get("https://docs.test/a")


def test_redirect_host_resolving_private_blocked(client_factory):
    def resolve(host):
        return ["10.0.0.5"] if host == "evil.test" else [PUBLIC]

    c, _ = client_factory(
        handler=lambda r: httpx.Response(302, headers={"location": "https://evil.test/x"}), resolve=resolve
    )
    with pytest.raises(FetchBlocked, match="private-address"):
        c.get("https://docs.test/a")


def test_initial_private_url_blocked_and_logged(client_factory):
    c, log_rows = client_factory(resolve=real)
    with pytest.raises(FetchBlocked, match="private-address"):
        c.get("http://localhost/x")
    assert c.requests == []
    assert log_rows()[-1]["error"] == "private-address"


def test_allow_private_network_opt_in(client_factory):
    c, _ = client_factory(resolve=real, allow_private_network=True)
    assert c.get("http://localhost/x").status == 200


def test_unresolvable_host_is_fetch_failed(client_factory):
    def resolve(host):
        raise FetchFailed("network: no such host")

    c, log_rows = client_factory(resolve=resolve)
    with pytest.raises(FetchFailed, match="network"):
        c.get("https://nope.test/")
    assert log_rows()[-1]["error"].startswith("network")


def test_scheme_and_private_checks():
    with pytest.raises(FetchBlocked, match="scheme"):
        check_url("ftp://docs.test/x", False, resolve=pub)
    with pytest.raises(FetchBlocked, match="scheme"):
        check_url("file:///etc/passwd", True, resolve=pub)
    with pytest.raises(FetchBlocked, match="private-address"):
        check_url("http://127.0.0.1/", False, resolve=real)
    with pytest.raises(FetchBlocked, match="private-address"):
        check_url("https://intranet.test/", False, resolve=lambda h: ["10.0.0.5"])
    check_url("http://127.0.0.1/", True, resolve=real)  # allowed when opted in
    check_url("https://docs.test/", False, resolve=pub)


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "100.100.100.200",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_blocked_addresses(addr):
    with pytest.raises(FetchBlocked, match="private-address"):
        check_url("https://x.test/", False, resolve=lambda h: [addr])


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/",
        "http://0.0.0.0/",
    ],
)
def test_literal_ip_hosts_checked_without_dns(url):
    def boom(host):
        raise AssertionError("DNS must not be consulted for literal IPs")

    with pytest.raises(FetchBlocked, match="private-address"):
        check_url(url, False, resolve=boom)


def test_any_blocked_address_blocks():
    with pytest.raises(FetchBlocked, match="private-address"):
        check_url("https://x.test/", False, resolve=lambda h: [PUBLIC, "10.0.0.1"])
    check_url("https://x.test/", False, resolve=lambda h: [PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"])


def test_default_resolve_failure_is_fetch_failed():
    from almanac.fetch.safety import default_resolve

    with pytest.raises(FetchFailed, match="^network: "):
        default_resolve("does-not-exist.invalid")


def test_noai_header_blocks(client_factory):
    c, log_rows = client_factory(
        handler=lambda r: httpx.Response(200, text="x", headers={"X-Robots-Tag": "noai, noimageai"})
    )
    with pytest.raises(FetchBlocked, match="noai"):
        c.get("https://docs.test/a")
    assert log_rows()[-1]["error"] == "noai"


def test_user_agent(client_factory):
    c, _ = client_factory(contact="me@example.com")
    assert c.user_agent == f"Almanac/{__version__} (+me@example.com)"
    c.get("https://docs.test/a")
    assert {r.headers["user-agent"] for r in c.requests} == {c.user_agent}
    c2, _ = client_factory()
    assert c2.user_agent == f"Almanac/{__version__}"


@pytest.mark.parametrize("url", ["https://docs.test/a\tb", "https://docs.test/a\x00b", "https://docs.test/a\nb"])
def test_malformed_url_is_logged_fetch_failed(client_factory, url):
    c, log_rows = client_factory()
    with pytest.raises(FetchFailed, match="invalid url"):
        c.get(url)
    assert page_requests(c) == []
    assert c.sleeps == []  # not retried
    row = log_rows()[-1]
    assert row["error"] == "network: invalid url" and row["status"] is None


@pytest.mark.parametrize("url", ["https://u:p@docs.test/x", "https://tok@docs.test/x"])
def test_check_url_blocks_credentials(url):
    with pytest.raises(FetchBlocked) as e:
        check_url(url, True)
    assert e.value.reason == "credentials"
