from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from fnmatch import fnmatch
from urllib.parse import urldefrag, urljoin, urlsplit

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from almanac.errors import FetchBlocked, FetchFailed
from almanac.fetch.http import HttpClient, HttpResult
from almanac.models import RawDoc, Settings, Source, SourceType, Technology
from almanac.store import Manifest

log = logging.getLogger(__name__)

MAX_SITEMAP_DEPTH = 3
_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_locs(xml: bytes, base: str) -> tuple[str, list[str]]:
    """Return ("sitemapindex" | "urlset", absolute <loc> values)."""
    root = ElementTree.fromstring(xml)
    locs = [
        urljoin(base, el.text.strip()) for el in root.iter() if _local(el.tag) == "loc" and el.text and el.text.strip()
    ]
    return _local(root.tag), locs


class WebFetcher:
    def __init__(self, http: HttpClient, settings: Settings):
        self._http = http
        self._settings = settings

    def fetch(self, source: Source, tech: Technology, manifest: Manifest, force: bool = False) -> Iterator[RawDoc]:
        if source.type is SourceType.llms_txt:
            assert source.url
            if urlsplit(source.url).path.endswith("llms-full.txt"):
                yield from self._pages([source.url], source, tech, manifest, force, content_type="text/markdown")
                return
            urls = self._llms_links(source.url)
        elif source.type is SourceType.sitemap:
            assert source.url
            urls = self._sitemap_urls(source.url, 1, set())
        elif source.type is SourceType.url_list:
            urls = list(source.urls)
        else:
            raise ValueError(f"unsupported web source type: {source.type.value}")
        candidates = self._select(urls, source)[: self._settings.max_pages_per_source]
        yield from self._pages(candidates, source, tech, manifest, force)

    def _index(self, url: str) -> HttpResult:
        """Fetch an index file unconditionally; any failure aborts the source."""
        try:
            return self._http.get(url)
        except FetchBlocked as e:
            raise FetchFailed(f"index blocked: {e.reason}") from e

    def _llms_links(self, url: str) -> list[str]:
        result = self._index(url)
        text = result.body.decode("utf-8", errors="replace")
        return [urljoin(result.final_url, m.group(1)) for m in _LINK_RE.finditer(text)]

    def _sitemap_urls(self, url: str, depth: int, seen: set[str]) -> list[str]:
        seen.add(url)
        result = self._index(url) if depth == 1 else self._child_index(url)
        if result is None:
            return []
        try:
            kind, locs = _parse_locs(result.body, result.final_url)
        except (ElementTree.ParseError, DefusedXmlException, ValueError) as e:
            if depth == 1:
                raise FetchFailed("invalid sitemap xml") from e
            log.warning("skipping unparseable sitemap %s", url)
            return []
        if kind != "sitemapindex":
            return locs
        if depth >= MAX_SITEMAP_DEPTH:
            log.warning("sitemap index nesting too deep at %s", url)
            return []
        urls: list[str] = []
        for child in locs:
            if child not in seen:
                urls.extend(self._sitemap_urls(child, depth + 1, seen))
        return urls

    def _child_index(self, url: str) -> HttpResult | None:
        try:
            return self._index(url)
        except FetchFailed:
            return None  # already recorded in the fetch log by HttpClient

    @staticmethod
    def _select(urls: list[str], source: Source) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in urls:
            url = urldefrag(raw)[0]
            if urlsplit(url).scheme not in ("http", "https") or url in seen:
                continue
            if source.include and not any(fnmatch(url, p) for p in source.include):
                continue
            if any(fnmatch(url, p) for p in source.exclude):
                continue
            seen.add(url)
            out.append(url)
        return out

    def _pages(
        self,
        urls: list[str],
        source: Source,
        tech: Technology,
        manifest: Manifest,
        force: bool,
        content_type: str | None = None,
    ) -> Iterator[RawDoc]:
        succeeded = 0
        for url in urls:
            etag, last_modified = (None, None) if force else manifest.http_validators(url)
            try:
                result = self._http.get(url, etag=etag, last_modified=last_modified)
            except (FetchBlocked, FetchFailed):
                continue  # HttpClient already wrote the outcome to the fetch log
            succeeded += 1
            if result.status == 304:
                now = datetime.now(UTC)  # a successful re-check: freshness moves forward
                manifest.touch_url(url, now)
                if result.final_url != url:
                    manifest.touch_url(result.final_url, now)
                continue
            manifest.set_http_validators(url, result.etag, result.last_modified)
            yield RawDoc(
                source_id=source.id,
                tech_id=tech.id,
                url=url,
                final_url=result.final_url,
                body=result.body,
                content_type=content_type or result.content_type or "text/html",
                fetched_at=datetime.now(UTC),
                etag=result.etag,
                last_modified=result.last_modified,
            )
        if urls and succeeded == 0:
            raise FetchFailed(f"all {len(urls)} urls failed")
