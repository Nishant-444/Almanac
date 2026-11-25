from __future__ import annotations

from urllib.parse import urlparse

import pytest

from almanac.db import connect
from almanac.errors import FetchFailed
from almanac.fetch.fetchlog import FetchLog
from almanac.fetch.http import HttpClient
from almanac.fetch.web import WebFetcher
from almanac.models import Settings, Source, Technology
from almanac.store import Manifest


def src(type_: str, url: str | None = None, **kw) -> Source:
    if type_ == "url_list":
        return Source(id="s", type=type_, kind="reference", trust=1, **kw)
    return Source(id="s", type=type_, url=url, kind="reference", trust=1, **kw)


TECH = Technology(id="react", name="React", sources=[])


@pytest.fixture
def fetcher_for(tmp_path):
    clients = []

    def factory(base_url=None, **overrides):
        values = {"allow_private_network": True, "rate_limit_per_host_rps": 100, **overrides}
        settings = Settings(**values)
        conn = connect(tmp_path / "t.db")
        client = HttpClient(settings, FetchLog(conn, "run1"), sleep=lambda s: None)
        clients.append(client)
        manifest = Manifest.load(tmp_path / "manifest.json")

        def log_rows():
            return [dict(r) for r in conn.execute("select * from fetch_log order by id")]

        return WebFetcher(client, settings), manifest, log_rows

    yield factory
    for c in clients:
        c.close()


def paths(docs):
    return sorted(urlparse(d.url).path for d in docs)


def test_llms_txt_fetches_links_skips_robots(docs_site, fetcher_for):
    f, manifest, log_rows = fetcher_for(docs_site)
    docs = list(f.fetch(src("llms_txt", f"{docs_site}/llms.txt"), TECH, manifest))
    assert paths(docs) == ["/reference/memo.html", "/reference/unicode.html", "/reference/use-memo.md"]
    assert any(r["error"] == "robots" and "/private/" in r["url"] for r in log_rows())
    assert docs[0].source_id == "s" and docs[0].tech_id == "react"
    assert docs[0].fetched_at.tzinfo is not None


def test_second_fetch_skips_unchanged(docs_site, fetcher_for):
    f, manifest, _ = fetcher_for(docs_site)
    s = src("llms_txt", f"{docs_site}/llms.txt")
    assert len(list(f.fetch(s, TECH, manifest))) == 3
    assert list(f.fetch(s, TECH, manifest)) == []
    assert len(list(f.fetch(s, TECH, manifest, force=True))) == 3


def test_sitemap_index_followed(docs_site, fetcher_for):
    f, manifest, _ = fetcher_for(docs_site)
    docs = list(f.fetch(src("sitemap", f"{docs_site}/sitemap-index.xml"), TECH, manifest))
    assert paths(docs) == ["/reference/memo.html"]


def test_sitemap_plain(docs_site, fetcher_for):
    f, manifest, _ = fetcher_for(docs_site)
    docs = list(f.fetch(src("sitemap", f"{docs_site}/sitemap.xml"), TECH, manifest))
    assert paths(docs) == ["/guides/perf.html", "/reference/memo.html"]


def test_include_exclude(docs_site, fetcher_for):
    f, manifest, _ = fetcher_for(docs_site)
    urls = [f"{docs_site}/reference/memo.html", f"{docs_site}/guides/perf.html"]
    docs = list(f.fetch(src("url_list", urls=urls, exclude=["*/guides/*"]), TECH, manifest))
    assert paths(docs) == ["/reference/memo.html"]
    docs = list(f.fetch(src("url_list", urls=urls, include=["*/guides/*"]), TECH, manifest))
    assert paths(docs) == ["/guides/perf.html"]


def test_page_cap(docs_site, fetcher_for):
    f, manifest, _ = fetcher_for(docs_site, max_pages_per_source=1)
    assert len(list(f.fetch(src("llms_txt", f"{docs_site}/llms.txt"), TECH, manifest))) == 1


def test_missing_link_logged_others_continue(docs_site, fetcher_for):
    f, manifest, log_rows = fetcher_for(docs_site)
    docs = list(f.fetch(src("llms_txt", f"{docs_site}/llms.txt"), TECH, manifest))
    assert len(docs) == 3
    assert any(r["status"] == 404 and r["url"].endswith("/missing.html") for r in log_rows())


def test_llms_full_single_doc(docs_site, fetcher_for):
    f, manifest, _ = fetcher_for(docs_site)
    docs = list(f.fetch(src("llms_txt", f"{docs_site}/llms-full.txt"), TECH, manifest))
    assert len(docs) == 1 and docs[0].content_type == "text/markdown"
    assert list(f.fetch(src("llms_txt", f"{docs_site}/llms-full.txt"), TECH, manifest)) == []


def test_unreachable_index_raises(fetcher_for):
    f, manifest, _ = fetcher_for()
    with pytest.raises(FetchFailed):
        list(f.fetch(src("llms_txt", "http://127.0.0.1:9/llms.txt"), TECH, manifest))


def test_all_urls_failed_raises(fetcher_for):
    f, manifest, _ = fetcher_for()
    with pytest.raises(FetchFailed, match="all 1 urls failed"):
        list(f.fetch(src("url_list", urls=["http://127.0.0.1:9/a.html"]), TECH, manifest))
