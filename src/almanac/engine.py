"""Read-only query engine: the one facade the MCP server and the CLI call."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from almanac.chunker import _FENCE_RE, _HEADING_RE, _fence_close_ok, heading_anchor
from almanac.db import connect
from almanac.errors import AlmanacError
from almanac.paths import AlmanacPaths
from almanac.query.retrieve import Retriever
from almanac.query.understand import understand
from almanac.registry import load_config
from almanac.response.bundle import build_bundle, not_found, validate_bundle
from almanac.store import Manifest, Store

_log = logging.getLogger("almanac.engine")
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*/[A-Za-z0-9._-]+/[a-z0-9-]+$")


def error_response(e: AlmanacError) -> dict:
    return {"status": "error", "code": e.code, "message": e.message, "details": e.details}


def extract_section(body: str, anchor: str) -> str | None:
    """Markdown from the heading whose anchor matches up to the next heading of the same or higher level."""
    lines: list[str] = []
    level = 0
    opener: str | None = None
    for line in body.splitlines():
        heading = None
        if opener is not None:
            if _fence_close_ok(line, opener):
                opener = None
        else:
            fence = _FENCE_RE.match(line)
            if fence:
                opener = fence.group(1)
            else:
                heading = _HEADING_RE.match(line)
        if level:
            if heading and len(heading.group(1)) <= level:
                break
            lines.append(line)
        elif heading and heading_anchor(heading.group(2)) == anchor:
            level = len(heading.group(1))
            lines.append(line)
    return "\n".join(lines).strip() + "\n" if lines else None


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Engine:
    def __init__(
        self,
        root: Path,
        *,
        strict: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = AlmanacPaths(root)
        self.config = load_config(self.paths.config)
        self.strict = strict
        self._clock = clock or (lambda: datetime.now(UTC))
        self._conn: sqlite3.Connection | None = None
        self._retriever: Retriever | None = None
        self._lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------

    def _db(self) -> sqlite3.Connection | None:
        if self._conn is None:
            if not self.paths.db.exists():
                return None
            self._conn = connect(self.paths.db, readonly=True, check_same_thread=False)
        return self._conn

    def _retr(self) -> Retriever:
        if self._retriever is None:
            conn = self._db()
            if conn is None:
                raise AlmanacError("E_INDEX_MISSING", "No index found; run: almanac fetch && almanac index")
            self._retriever = Retriever(conn)
        return self._retriever

    def _guard(self, fn: Callable[[], dict]) -> dict:
        try:
            with self._lock:
                return fn()
        except AlmanacError as e:
            return error_response(e)
        except Exception:  # noqa: BLE001 - the facade must never leak a traceback
            return self._internal()

    @staticmethod
    def _internal() -> dict:
        error_id = uuid.uuid4().hex[:8]
        _log.exception("Unexpected failure (error %s)", error_id)
        return error_response(AlmanacError("E_INTERNAL", f"Unexpected failure; see logs (error {error_id})"))

    def _write_log(self, entry: dict) -> None:
        try:
            self.paths.logs.mkdir(parents=True, exist_ok=True)
            with self.paths.query_log.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            _log.exception("Could not append to the query log")

    # -- search_docs ------------------------------------------------------

    def search_docs(
        self,
        query: str,
        tech: str | None = None,
        version: str | None = None,
        project_path: str | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        started = time.monotonic()
        entry: dict = {"query": query, "summary": None, "tech": tech, "version": version, "intent": None, "hits": 0}

        def run() -> dict:
            if not isinstance(query, str) or not query.strip():
                raise AlmanacError("E_BAD_INPUT", "query must be a non-empty string")
            if project_path is not None and not Path(project_path).is_dir():
                raise AlmanacError("E_BAD_INPUT", f"project_path is not an existing directory: {project_path}")
            retriever = self._retr()
            i = understand(query, self.config, retriever.indexed_versions(), tech=tech, version=version)
            entry.update(summary=i.summary, tech=i.tech, version=i.version, intent=i.intent)
            hits = retriever.search(i)
            entry["hits"] = len(hits)
            if not hits:
                nearby: list[str] = []
                for h in retriever.search(i, k=10, any_version=True):
                    if h.title not in nearby:
                        nearby.append(h.title)
                result = not_found(i, nearby[:3])
                entry.update(outcome="not_found", query_id=None)
                return result
            s = self.config.settings
            bundle = build_bundle(
                i,
                hits,
                max_tokens=max_tokens or s.response_token_budget,
                stale_after_days=s.stale_after_days,
                now=self._clock(),
                ms=int((time.monotonic() - started) * 1000),
            )
            violations = validate_bundle(bundle)
            if violations:
                if self.strict:
                    raise RuntimeError("bundle failed grounding check: " + "; ".join(violations))
                known = {src["ref"] for src in bundle["sources"]}
                bundle["excerpts"] = [
                    e for e in bundle["excerpts"] if e.get("refs") and all(r in known for r in e["refs"])
                ]
                remaining = validate_bundle(bundle)
                if remaining:
                    raise RuntimeError("bundle failed grounding check: " + "; ".join(remaining))
            entry.update(outcome="ok", query_id=bundle["meta"]["query_id"])
            return bundle

        result = self._guard(run)
        if result.get("status") == "error":
            entry.update(outcome=result["code"], query_id=None)
        entry["ms"] = int((time.monotonic() - started) * 1000)
        at = self._clock().astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ordered = {"at": at, "query_id": entry.pop("query_id", None), **entry}
        self._write_log(ordered)
        return result

    # -- get_page ---------------------------------------------------------

    def _resolve_id(self, id: str | None, tech: str | None, path: str | None, version: str | None) -> str:
        if id is None:
            if not tech or not path:
                raise AlmanacError("E_BAD_INPUT", "Provide either id, or tech and path")
            t = self.config.tech(tech)
            if t is None:
                raise AlmanacError("E_UNKNOWN_TECH", f"Unknown technology: {tech}")
            id = f"{t.id}/{version or t.stored_version()}/{path}"
        if not _ID_PATTERN.fullmatch(id) or any(seg in (".", "..") for seg in id.split("/")):
            raise AlmanacError("E_BAD_INPUT", f"Invalid page id: {id!r}")
        docs = self.paths.docs.resolve()
        if not docs.joinpath(id + ".md").resolve().is_relative_to(docs):
            raise AlmanacError("E_BAD_INPUT", f"Invalid page id: {id!r}")
        return id

    def get_page(
        self,
        id: str | None = None,
        tech: str | None = None,
        path: str | None = None,
        version: str | None = None,
        section: str | None = None,
    ) -> dict:
        def run() -> dict:
            page_id = self._resolve_id(id, tech, path, version)
            manifest = Manifest.load(self.paths.manifest)
            store = Store(self.paths.docs, manifest)
            try:
                page = store.read(page_id)
            except FileNotFoundError:
                raise AlmanacError("E_NOT_FOUND", f"No such page: {page_id}") from None
            markdown = page.body
            if section:
                found = extract_section(page.body, section)
                if found is None:
                    raise AlmanacError("E_NOT_FOUND", f"No section {section!r} in {page_id}")
                markdown = found
            entry = manifest.page_entry(page_id)
            fetched_at = (entry or {}).get("fetched_at") or _iso(page.fetched_at)  # manifest is authoritative
            return {
                "status": "ok",
                "id": page.id,
                "title": page.title,
                "version": page.version,
                "source_url": page.source_url,
                "fetched_at": fetched_at,
                "flags": list(page.flags),
                "markdown": markdown,
            }

        return self._guard(run)

    # -- list_technologies ------------------------------------------------

    def list_technologies(self) -> dict:
        def run() -> dict:
            versions: dict[str, list[str]] = {}
            stats: dict[str, tuple[int, str | None]] = {}
            conn = self._db()
            if conn is not None:
                try:
                    versions = self._retr().indexed_versions()
                    rows = conn.execute(
                        "SELECT tech_id, COUNT(*) AS n, MAX(fetched_at) AS last FROM page "
                        "WHERE status = 'active' GROUP BY tech_id"
                    ).fetchall()
                    stats = {r["tech_id"]: (r["n"], r["last"]) for r in rows}
                except AlmanacError as e:
                    if e.code != "E_INDEX_MISSING":
                        raise
            techs = [
                {
                    "id": t.id,
                    "name": t.name,
                    "versions_indexed": versions.get(t.id, []),
                    "page_count": stats.get(t.id, (0, None))[0],
                    "last_fetched": stats.get(t.id, (0, None))[1],
                }
                for t in self.config.technologies
            ]
            return {"status": "ok", "technologies": techs}

        return self._guard(run)
