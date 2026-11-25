from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

# DR-03 with deviations: TEXT source ids, page.page_key, no query_log, meta table, FTS triggers.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tech (id TEXT PRIMARY KEY, name TEXT, homepage TEXT, aliases TEXT);
CREATE TABLE IF NOT EXISTS source (id TEXT PRIMARY KEY, tech_id TEXT REFERENCES tech(id),
  type TEXT, url TEXT, kind TEXT, trust INTEGER, interval_hours INTEGER,
  last_fetch TEXT, last_status TEXT);
CREATE TABLE IF NOT EXISTS page (id INTEGER PRIMARY KEY, source_id TEXT REFERENCES source(id),
  tech_id TEXT, version TEXT, path TEXT UNIQUE, url TEXT, title TEXT, kind TEXT,
  trust INTEGER, content_hash TEXT, fetched_at TEXT, flags TEXT, status TEXT,
  page_key TEXT UNIQUE, meta_hash TEXT);
CREATE TABLE IF NOT EXISTS chunk (id INTEGER PRIMARY KEY, page_id INTEGER REFERENCES page(id),
  ord INTEGER, heading_path TEXT, anchor TEXT, text TEXT, tokens INTEGER, topics TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(text, heading_path, topics,
  content='chunk', content_rowid='id', tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS chunk_vec (chunk_id INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS symbol (id INTEGER PRIMARY KEY, tech_id TEXT, version TEXT,
  name TEXT, kind TEXT, chunk_id INTEGER REFERENCES chunk(id));
CREATE TABLE IF NOT EXISTS fetch_log (id INTEGER PRIMARY KEY, run_id TEXT, url TEXT,
  status INTEGER, bytes INTEGER, ms INTEGER, error TEXT, at TEXT, source_id TEXT);
CREATE INDEX IF NOT EXISTS idx_page_tech_version ON page(tech_id, version);
CREATE INDEX IF NOT EXISTS idx_chunk_page ON chunk(page_id);
CREATE INDEX IF NOT EXISTS idx_symbol_name ON symbol(tech_id, name);

CREATE TRIGGER IF NOT EXISTS chunk_ai AFTER INSERT ON chunk BEGIN
  INSERT INTO chunk_fts(rowid, text, heading_path, topics)
  VALUES (new.id, new.text, new.heading_path, new.topics);
END;
CREATE TRIGGER IF NOT EXISTS chunk_ad AFTER DELETE ON chunk BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, text, heading_path, topics)
  VALUES ('delete', old.id, old.text, old.heading_path, old.topics);
END;
CREATE TRIGGER IF NOT EXISTS chunk_au AFTER UPDATE ON chunk BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, text, heading_path, topics)
  VALUES ('delete', old.id, old.text, old.heading_path, old.topics);
  INSERT INTO chunk_fts(rowid, text, heading_path, topics)
  VALUES (new.id, new.text, new.heading_path, new.topics);
END;
"""


def connect(path: Path, readonly: bool = False, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the Almanac database. Read-only never creates the file, directory or schema."""
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    if "source_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(fetch_log)")}:
        conn.execute("ALTER TABLE fetch_log ADD COLUMN source_id TEXT")  # databases created before this column
    if "meta_hash" not in {r["name"] for r in conn.execute("PRAGMA table_info(page)")}:
        conn.execute("ALTER TABLE page ADD COLUMN meta_hash TEXT")  # NULL: re-indexed once on the next run
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn
