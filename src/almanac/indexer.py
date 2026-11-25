from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from almanac.chunker import chunk_page
from almanac.models import Config, Page
from almanac.store import Store, metadata_digest


@dataclass
class IndexStats:
    pages_indexed: int = 0
    pages_skipped: int = 0
    pages_deleted: int = 0
    chunks_written: int = 0


def _iso(dt: datetime) -> str:
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


class Indexer:
    def __init__(self, conn: sqlite3.Connection, store: Store, config: Config) -> None:
        self.conn = conn
        self.store = store
        self.config = config

    def index(self, *, tech: str | None = None, rebuild: bool = False) -> IndexStats:
        conn = self.conn
        previous = conn.isolation_level
        conn.commit()
        conn.isolation_level = None  # explicit transaction control; no implicit BEGINs
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                stats = self._run(tech, rebuild)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return stats
        finally:
            conn.isolation_level = previous

    def _run(self, tech: str | None, rebuild: bool) -> IndexStats:
        conn = self.conn
        stats = IndexStats()
        known_sources = self._upsert_config()
        cols = "SELECT page_key, content_hash, meta_hash FROM page"
        existing = {
            r["page_key"]: (r["content_hash"], r["meta_hash"])
            for r in (conn.execute(cols + " WHERE tech_id = ?", (tech,)) if tech else conn.execute(cols))
        }
        seen: set[str] = set()
        for page in self.store.iter_pages(tech):
            seen.add(page.id)
            entry = self.store.manifest.page_entry(page.id)
            fetched = entry["fetched_at"] if entry and entry.get("fetched_at") else _iso(page.fetched_at)
            if not rebuild and existing.get(page.id) == (page.content_hash, metadata_digest(page)):
                conn.execute("UPDATE page SET fetched_at = ? WHERE page_key = ?", (fetched, page.id))
                stats.pages_skipped += 1
                continue
            stats.chunks_written += self._write_page(page, fetched, known_sources)
            stats.pages_indexed += 1
        for key in existing.keys() - seen:
            self._delete_page(key)
            stats.pages_deleted += 1
        return stats

    def _upsert_config(self) -> set[str]:
        conn = self.conn
        default_hours = self.config.settings.default_refresh_hours
        sources: set[str] = set()
        for t in self.config.technologies:
            conn.execute(
                "INSERT INTO tech(id, name, homepage, aliases) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, homepage = excluded.homepage, "
                "aliases = excluded.aliases",
                (t.id, t.name, t.homepage, json.dumps(t.aliases)),
            )
            for s in t.sources:
                conn.execute(
                    "INSERT INTO source(id, tech_id, type, url, kind, trust, interval_hours) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET tech_id = excluded.tech_id, type = excluded.type, "
                    "url = excluded.url, kind = excluded.kind, trust = excluded.trust, "
                    "interval_hours = excluded.interval_hours",
                    (
                        s.id,
                        t.id,
                        s.type.value,
                        s.url or (s.urls[0] if s.urls else None),
                        s.kind.value,
                        s.trust,
                        t.refresh_hours or default_hours,
                    ),
                )
                sources.add(s.id)
        return sources

    def _write_page(self, page: Page, fetched: str, known_sources: set[str]) -> int:
        conn = self.conn
        row = conn.execute("SELECT id FROM page WHERE page_key = ?", (page.id,)).fetchone()
        if row is not None:
            self._delete_chunks(row["id"])
        source_id = page.source_id if page.source_id in known_sources else None
        values = (
            source_id,
            page.tech,
            page.version,
            self.store.path_for(page.id).relative_to(self.store.docs_dir).as_posix(),
            page.source_url,
            page.title,
            str(page.kind),
            page.trust,
            page.content_hash,
            fetched,
            json.dumps(page.flags),
            "active",
            page.id,
            metadata_digest(page),
        )
        conn.execute(
            "INSERT INTO page(source_id, tech_id, version, path, url, title, kind, trust, content_hash, "
            "fetched_at, flags, status, page_key, meta_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(page_key) DO UPDATE SET source_id = excluded.source_id, tech_id = excluded.tech_id, "
            "version = excluded.version, path = excluded.path, url = excluded.url, title = excluded.title, "
            "kind = excluded.kind, trust = excluded.trust, content_hash = excluded.content_hash, "
            "fetched_at = excluded.fetched_at, flags = excluded.flags, status = excluded.status, "
            "meta_hash = excluded.meta_hash",
            values,
        )
        page_row_id = conn.execute("SELECT id FROM page WHERE page_key = ?", (page.id,)).fetchone()["id"]
        chunks = chunk_page(page)
        for c in chunks:
            conn.execute(
                "INSERT INTO chunk(page_id, ord, heading_path, anchor, text, tokens, topics) "
                "VALUES (?, ?, ?, ?, ?, ?, '')",
                (page_row_id, c.ord, c.heading_path, c.anchor, c.text, c.tokens),
            )
        return len(chunks)

    def _delete_chunks(self, page_row_id: int) -> None:
        conn = self.conn
        ids = "SELECT id FROM chunk WHERE page_id = ?"
        conn.execute(f"DELETE FROM chunk_vec WHERE chunk_id IN ({ids})", (page_row_id,))
        conn.execute(f"DELETE FROM symbol WHERE chunk_id IN ({ids})", (page_row_id,))
        conn.execute("DELETE FROM chunk WHERE page_id = ?", (page_row_id,))  # triggers sync FTS

    def _delete_page(self, page_key: str) -> None:
        row = self.conn.execute("SELECT id FROM page WHERE page_key = ?", (page_key,)).fetchone()
        if row is not None:
            self._delete_chunks(row["id"])
            self.conn.execute("DELETE FROM page WHERE id = ?", (row["id"],))
