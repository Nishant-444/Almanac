from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from almanac.models import ChangeKind, Kind, Page

log = logging.getLogger("almanac.store")

_SUFFIX_RE = re.compile(r"\.(html|htm|md|mdx|txt)$")
_SLUG_MAX = 120
_FENCE = "---\n"

# DR-02 field order; optional fields are omitted when they hold their default.
_REQUIRED_KEYS = ("id", "tech", "version", "kind", "trust", "title", "source_url", "source_id", "fetched_at")
_TAIL_KEYS = ("content_hash", "last_modified", "flags", "description", "canonical_url", "licence", "extra")
# Front-matter that config edits or re-cleaning can change without touching the body (fetched_at excluded:
# freshness lives in the manifest).
_META_KEYS = ("kind", "trust", "title", "flags", "source_url", "canonical_url", "licence", "description")


def slugify_url(url: str) -> str:
    from urllib.parse import urlsplit

    path = _SUFFIX_RE.sub("", urlsplit(url).path.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", path).strip("-")
    return slug[:_SLUG_MAX].strip("-") or "index"


def content_hash(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def identity_url(final_url: str, extra: dict[str, str]) -> str:
    """The URL a page is identified by: a fetcher-supplied stable key (e.g. git, sha-free) or the final URL."""
    return extra.get("stable_url") or final_url


def page_metadata(page: Page) -> dict[str, Any]:
    return {
        k: (str(v) if k == "kind" else list(v) if k == "flags" else v) for k in _META_KEYS for v in [getattr(page, k)]
    }


def metadata_digest(page: Page) -> str:
    """Digest of the front-matter metadata the index depends on (the body has its own content_hash)."""
    data = json.dumps(page_metadata(page), sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 1_000_000
    y.sort_base_mapping_type_on_output = False
    return y


def _fmt_dt(dt: datetime) -> str:
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def page_to_text(page: Page) -> str:
    data: dict[str, Any] = {}
    for key in _REQUIRED_KEYS:
        value = getattr(page, key)
        if key == "kind":
            value = str(value)
        elif key == "fetched_at":
            value = _fmt_dt(value)
        data[key] = value
    for key in _TAIL_KEYS:
        value = getattr(page, key)
        if key in ("content_hash", "flags") or value:
            data[key] = list(value) if key == "flags" else value
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return f"{_FENCE}{buf.getvalue()}{_FENCE}{page.body}"


def page_from_text(text: str) -> Page:
    if not text.startswith(_FENCE):
        raise ValueError("page text has no front-matter")
    end = text.find("\n" + _FENCE, len(_FENCE) - 1)
    if end == -1:
        raise ValueError("page front-matter is not terminated")
    meta = _yaml().load(text[len(_FENCE) : end + 1]) or {}
    body = text[end + 1 + len(_FENCE) :]
    known = {f.name for f in fields(Page)}
    kwargs = {k: v for k, v in meta.items() if k in known and k != "body"}
    kwargs["kind"] = Kind(kwargs["kind"])
    kwargs["fetched_at"] = _parse_dt(kwargs["fetched_at"])
    if kwargs.get("last_modified") is not None:
        kwargs["last_modified"] = str(kwargs["last_modified"])
    return Page(**kwargs, body=body)


class Manifest:
    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        data = data or {}
        self._http: dict[str, dict[str, str | None]] = data.get("http", {})
        self._sources: dict[str, dict[str, Any]] = data.get("sources", {})
        self._pages: dict[str, dict[str, Any]] = data.get("pages", {})

    @classmethod
    def load(cls, path: Path) -> Manifest:
        if not path.exists():
            return cls(path)
        return cls(path, json.loads(path.read_text(encoding="utf-8")))

    def http_validators(self, url: str) -> tuple[str | None, str | None]:
        entry = self._http.get(url, {})
        return entry.get("etag"), entry.get("last_modified")

    def set_http_validators(self, url: str, etag: str | None, last_modified: str | None) -> None:
        self._http[url] = {"etag": etag, "last_modified": last_modified}

    def source_state(self, source_id: str) -> dict[str, Any]:
        return dict(self._sources.get(source_id, {}))

    def set_source_state(self, source_id: str, **values: Any) -> None:
        self._sources.setdefault(source_id, {}).update(values)

    def page_entry(self, page_id: str) -> dict[str, Any] | None:
        entry = self._pages.get(page_id)
        return dict(entry) if entry is not None else None

    def record_page(self, page: Page, path: Path) -> None:
        self._pages[page.id] = {
            "path": path.as_posix(),
            "tech": page.tech,
            "version": page.version,
            "source_url": page.source_url,
            "key": identity_url(page.source_url, page.extra),
            "source_id": page.source_id,
            "content_hash": page.content_hash,
            "fetched_at": _fmt_dt(page.fetched_at),
        }

    def page_id_for_url(self, tech: str, version: str, url: str) -> str | None:
        """The page id already stored for an identity URL (see identity_url)."""
        for page_id, e in self._pages.items():
            if e["tech"] == tech and e["version"] == version and e.get("key", e["source_url"]) == url:
                return page_id
        return None

    def touch_url(self, url: str, when: datetime) -> None:
        """A successful re-check (e.g. HTTP 304) of url: mark its pages fresh."""
        for e in self._pages.values():
            if e["source_url"] == url:
                e["fetched_at"] = _fmt_dt(when)

    def touch_source(self, source_id: str, when: datetime) -> None:
        """A successful re-check of a whole source (e.g. unchanged git sha): mark its pages fresh."""
        for e in self._pages.values():
            if e.get("source_id") == source_id:
                e["fetched_at"] = _fmt_dt(when)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"http": self._http, "sources": self._sources, "pages": self._pages}
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


class Store:
    def __init__(self, docs_dir: Path, manifest: Manifest) -> None:
        self.docs_dir = docs_dir
        self.manifest = manifest

    def make_page_id(self, tech: str, version: str, url: str) -> str:
        existing = self.manifest.page_id_for_url(tech, version, url)
        if existing:
            return existing
        candidate = f"{tech}/{version}/{slugify_url(url)}"
        if self.manifest.page_entry(candidate) is not None or self.path_for(candidate).exists():
            candidate += "-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        return candidate

    def path_for(self, page_id: str) -> Path:
        parts = page_id.split("/")
        if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
            raise ValueError(f"invalid page id: {page_id!r}")
        return self.docs_dir.joinpath(*parts[:-1], parts[-1] + ".md")

    def put(self, page: Page) -> ChangeKind:
        digest = content_hash(page.body)
        page = replace(page, content_hash=digest)
        path = self.path_for(page.id)
        entry = self.manifest.page_entry(page.id)
        if entry is None or not path.exists():
            kind = ChangeKind.new
        elif entry["content_hash"] == digest and self._same_metadata(path, page):
            # Body and metadata unchanged: leave the file alone, but freshness lives in the manifest.
            self.manifest.record_page(page, path.relative_to(self.docs_dir))
            return ChangeKind.unchanged
        else:
            kind = ChangeKind.changed
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")  # same dir, so os.replace is atomic
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(page_to_text(page))
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self.manifest.record_page(page, path.relative_to(self.docs_dir))
        return kind

    @staticmethod
    def _same_metadata(path: Path, page: Page) -> bool:
        try:
            with path.open(encoding="utf-8", newline="") as fh:
                stored = page_from_text(fh.read())
        except Exception:  # noqa: BLE001 - an unreadable stored file is simply rewritten
            return False
        return page_metadata(stored) == page_metadata(page)

    def read(self, page_id: str) -> Page:
        with self.path_for(page_id).open(encoding="utf-8", newline="") as fh:
            return page_from_text(fh.read())

    def iter_pages(self, tech: str | None = None) -> Iterator[Page]:
        root = self.docs_dir / tech if tech else self.docs_dir
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*.md")):
            try:
                with path.open(encoding="utf-8", newline="") as fh:
                    text = fh.read()
                if not text.startswith("---"):
                    log.info("skipping %s: no front-matter (not a stored page)", path)
                    continue
                page = page_from_text(text)
            except Exception as e:  # noqa: BLE001 - one bad file must not stop indexing
                log.warning("skipping unreadable page file %s: %s", path, e)
                continue
            yield page
