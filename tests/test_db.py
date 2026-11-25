from __future__ import annotations

import sqlite3

import pytest

from almanac.db import SCHEMA_VERSION, connect


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("select name from sqlite_master where type in ('table')")}


def test_schema_created_and_versioned(tmp_path):
    conn = connect(tmp_path / "sub" / "a.db")
    assert SCHEMA_VERSION == "1"
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "1"
    names = table_names(conn)
    assert {"tech", "source", "page", "chunk", "chunk_fts", "chunk_vec", "symbol", "fetch_log", "meta"} <= names
    assert "query_log" not in names


def test_connect_is_idempotent_and_rows_are_named(tmp_path):
    connect(tmp_path / "a.db").close()
    conn = connect(tmp_path / "a.db")
    assert conn.execute("select count(*) as n from meta").fetchone()["n"] == 1


def test_source_ids_are_text_and_page_key_unique(tmp_path):
    conn = connect(tmp_path / "a.db")
    conn.execute("insert into tech(id, name) values ('react', 'React')")
    conn.execute("insert into source(id, tech_id) values ('react-docs', 'react')")
    conn.execute("insert into page(source_id, path, page_key) values ('react-docs', 'p1', 'react/19/memo')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("insert into page(source_id, path, page_key) values ('react-docs', 'p2', 'react/19/memo')")


def test_fts_triggers_keep_index_in_sync(tmp_path):
    conn = connect(tmp_path / "a.db")
    conn.execute("insert into source(id, tech_id) values ('s', null)")
    conn.execute("insert into page(source_id, path, page_key) values ('s', 'p', 'k')")
    conn.execute("insert into chunk(page_id, ord, heading_path, text) values (1, 0, 'Hooks', 'memoizing running dogs')")

    def hits(q: str) -> int:
        return conn.execute("select count(*) from chunk_fts where chunk_fts match ?", (q,)).fetchone()[0]

    assert hits("memoize") == 1  # porter stemming
    assert hits("hooks") == 1  # heading_path indexed
    conn.execute("update chunk set text = 'completely different' where id = 1")
    assert hits("dogs") == 0
    assert hits("different") == 1
    conn.execute("delete from chunk where id = 1")
    assert hits("different") == 0


def test_readonly_connection_cannot_write(tmp_path):
    connect(tmp_path / "a.db").close()
    ro = connect(tmp_path / "a.db", readonly=True)
    assert ro.execute("select count(*) from meta").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("insert into meta values ('x', 'y')")


def test_readonly_does_not_create(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "missing" / "a.db", readonly=True)
    assert not (tmp_path / "missing").exists()


def test_connect_adds_source_id_to_old_fetch_log(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE fetch_log (id INTEGER PRIMARY KEY, run_id TEXT, url TEXT, status INTEGER, bytes INTEGER, ms INTEGER, error TEXT, at TEXT)"
    )
    raw.commit()
    raw.close()
    for _ in range(2):  # idempotent
        conn = connect(path)
        assert "source_id" in {r["name"] for r in conn.execute("PRAGMA table_info(fetch_log)")}
        conn.close()
