"""Build pipeline: configured sources -> stored pages -> search index."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from almanac import db
from almanac.cleaner import clean, detect_version
from almanac.fetch.fetchlog import FetchLog
from almanac.fetch.git import GitFetcher
from almanac.fetch.http import HttpClient
from almanac.fetch.web import WebFetcher
from almanac.indexer import Indexer, IndexStats
from almanac.models import ChangeKind, Source, SourceType, Technology
from almanac.paths import AlmanacPaths
from almanac.registry import load_config
from almanac.store import Manifest, Store, identity_url

log = logging.getLogger(__name__)

NOT_SUPPORTED = "not supported until v1.0"
_WEB_TYPES = {SourceType.llms_txt, SourceType.sitemap, SourceType.url_list}


class SelectionError(ValueError):
    """The requested tech or source does not exist in the config."""


@dataclass
class FetchReport:
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    changes: dict[ChangeKind, int] = field(default_factory=lambda: {k: 0 for k in ChangeKind})


def _select(config, tech: str | None, source: str | None) -> list[tuple[Technology, Source]]:
    techs = config.technologies
    if tech is not None:
        found = config.tech(tech)
        if found is None:
            raise SelectionError(f"unknown technology '{tech}'")
        techs = [found]
    pairs = [(t, s) for t in techs for s in t.sources if s.enabled]
    if source is not None:
        pairs = [(t, s) for t, s in pairs if s.id == source]
        if not pairs:
            raise SelectionError(f"unknown or disabled source '{source}'")
    return pairs


def run_fetch(
    root: Path,
    *,
    tech: str | None = None,
    source: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchReport:
    paths = AlmanacPaths(root)
    # settings.data_dir is ignored in the MVP: data always lives at paths.data.
    config = load_config(paths.config)
    pairs = _select(config, tech, source)
    report = FetchReport()
    if dry_run:
        for _, src in pairs:
            if src.type in _WEB_TYPES or src.type is SourceType.git:
                report.ok.append(src.id)
            else:
                report.skipped[src.id] = NOT_SUPPORTED
        return report

    run_id = uuid4().hex[:12]
    manifest = Manifest.load(paths.manifest)
    store = Store(paths.docs, manifest)
    settings = config.settings
    conn = db.connect(paths.db)  # dedicated connection: FetchLog commits on it
    fetch_log = FetchLog(conn, run_id)
    http = HttpClient(settings, fetch_log, sleep=sleep)
    web = WebFetcher(http, settings)
    git = GitFetcher(settings, paths.git_cache, fetch_log)
    try:
        for t, src in pairs:
            if src.type not in _WEB_TYPES and src.type is not SourceType.git:
                report.skipped[src.id] = NOT_SUPPORTED
                log.info("skipping source %s: %s", src.id, NOT_SUPPORTED)
                continue
            fetch_log.source_id = src.id
            fetcher = git if src.type is SourceType.git else web
            current_url: str | None = None
            try:
                for raw in fetcher.fetch(src, t, manifest, force):
                    current_url = raw.url
                    key = identity_url(raw.final_url, raw.extra)
                    version = detect_version(key, src, t)
                    page_id = store.make_page_id(t.id, version, key)
                    page = clean(raw, src, t, page_id, settings.min_content_chars)
                    report.changes[store.put(page)] += 1
                    current_url = None
                report.ok.append(src.id)
            except Exception as e:  # noqa: BLE001 - one bad source must not stop the run (FR-SCH-05)
                if current_url is not None:
                    # The fetcher already recorded validators for a page we never stored.
                    manifest.set_http_validators(current_url, None, None)
                report.failed[src.id] = getattr(e, "reason", None) or str(e) or type(e).__name__
                log.warning("source %s failed: %s", src.id, report.failed[src.id])
            finally:
                manifest.save()
        manifest.save()
    finally:
        http.close()
        conn.close()
    return report


def run_index(root: Path, *, tech: str | None = None, rebuild: bool = False) -> IndexStats:
    paths = AlmanacPaths(root)
    config = load_config(paths.config)
    if tech is not None:
        found = config.tech(tech)
        if found is None:
            raise SelectionError(f"unknown technology '{tech}'")
        tech = found.id
    store = Store(paths.docs, Manifest.load(paths.manifest))
    conn = db.connect(paths.db)
    try:
        return Indexer(conn, store, config).index(tech=tech, rebuild=rebuild)
    finally:
        conn.close()


__all__ = ["FetchReport", "SelectionError", "run_fetch", "run_index"]
