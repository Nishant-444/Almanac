import shutil

import pytest

from almanac.db import connect
from almanac.indexer import Indexer
from almanac.models import Config, Kind, Source, Technology
from almanac.store import Manifest, Store

BODY_A = "# Memo\n\nuseMemo caches a memo value between renders. zebra stripes here.\n"
BODY_B = "# Effect\n\nuseEffect runs side effects after render, memo free.\n"


def fts_count(conn, term):
    return conn.execute("SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH ?", (term,)).fetchone()[0]


def counts(conn):
    return tuple(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("page", "chunk"))


def chunk_texts(conn):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT p.page_key, c.ord, c.text FROM chunk c JOIN page p ON p.id = c.page_id ORDER BY 1, 2, 3"
        )
    ]


def build_store(root, make_page, bodies):
    store = Store(root / "docs", Manifest(root / "manifest.json"))
    for slug, body in bodies.items():
        store.put(make_page(id=f"react/19/{slug}", title=slug, body=body))
    return store


@pytest.fixture
def store_with_two_pages(tmp_path, make_page):
    return build_store(tmp_path, make_page, {"memo": BODY_A, "effect": BODY_B})


@pytest.fixture
def cfg():
    src = Source(id="react-docs", type="llms_txt", url="https://example.org/llms.txt", kind=Kind.reference, trust=1)
    return Config(technologies=[Technology(id="react", name="React", sources=[src], refresh_hours=6)])


def test_index_then_skip_unchanged(store_with_two_pages, cfg, tmp_path):
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    s1 = ix.index()
    s2 = ix.index()
    assert (s1.pages_indexed, s2.pages_indexed, s2.pages_skipped) == (2, 0, 2)
    assert s1.chunks_written > 0
    assert fts_count(conn, "memo") > 0
    row = conn.execute("SELECT * FROM page WHERE page_key = 'react/19/memo'").fetchone()
    assert row["status"] == "active" and row["flags"] == "[]" and row["kind"] == "reference"
    assert conn.execute("SELECT interval_hours FROM source").fetchone()[0] == 6


def test_config_upsert_keeps_run_state(store_with_two_pages, cfg, tmp_path):
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    ix.index()
    conn.execute("UPDATE source SET last_fetch = 'x', last_status = 'ok'")
    conn.commit()
    ix.index()
    assert tuple(conn.execute("SELECT last_fetch, last_status FROM source").fetchone()) == ("x", "ok")


def test_changed_page_replaces_chunks_in_fts(store_with_two_pages, cfg, tmp_path, make_page):
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    ix.index()
    assert fts_count(conn, "zebra") > 0
    store_with_two_pages.put(make_page(id="react/19/memo", title="memo", body="# Memo\n\nuseMemo only now.\n"))
    s = ix.index()
    assert s.pages_indexed == 1
    assert fts_count(conn, "zebra") == 0
    assert fts_count(conn, "only") > 0


def test_deleted_file_removed_from_index(store_with_two_pages, cfg, tmp_path):
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    ix.index()
    store_with_two_pages.path_for("react/19/memo").unlink()
    s = ix.index()
    assert s.pages_deleted == 1
    assert fts_count(conn, "zebra") == 0
    assert conn.execute("SELECT count(*) FROM page").fetchone()[0] == 1


def test_rebuild_reindexes_all(store_with_two_pages, cfg, tmp_path):
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    ix.index()
    assert ix.index(rebuild=True).pages_indexed == 2
    assert fts_count(conn, "zebra") == 1


def test_failure_rolls_back(monkeypatch, store_with_two_pages, cfg, tmp_path, make_page):
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    ix.index()
    before = (counts(conn), chunk_texts(conn))
    store_with_two_pages.put(make_page(id="react/19/memo", title="memo", body="# Memo\n\nchanged one.\n"))
    store_with_two_pages.put(make_page(id="react/19/effect", title="effect", body="# E\n\nchanged two.\n"))
    import almanac.indexer as mod

    real = mod.chunk_page
    calls = []

    def boom(page, *a, **k):
        calls.append(page.id)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(page, *a, **k)

    monkeypatch.setattr("almanac.indexer.chunk_page", boom)
    with pytest.raises(RuntimeError):
        ix.index()
    assert (counts(conn), chunk_texts(conn)) == before
    assert fts_count(conn, "zebra") > 0
    assert not conn.in_transaction
    monkeypatch.undo()
    assert ix.index().pages_indexed == 2


def test_manifest_fetched_at_refreshes_skipped_page(store_with_two_pages, cfg, tmp_path):
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    ix.index()
    new = "2026-12-01T00:00:00Z"
    store_with_two_pages.manifest._pages["react/19/memo"]["fetched_at"] = new
    s = ix.index()
    assert s.pages_indexed == 0
    got = conn.execute("SELECT fetched_at FROM page WHERE page_key = 'react/19/memo'").fetchone()[0]
    assert got == new


def test_fetched_at_falls_back_to_front_matter(tmp_path, make_page, cfg):
    store = build_store(tmp_path, make_page, {"memo": BODY_A})
    del store.manifest._pages["react/19/memo"]
    conn = connect(tmp_path / "a.db")
    Indexer(conn, store, cfg).index()
    assert conn.execute("SELECT fetched_at FROM page").fetchone()[0] == "2026-11-24T06:10:00Z"


def test_tech_filter_limits_deletes(store_with_two_pages, cfg, tmp_path, make_page):
    store_with_two_pages.put(make_page(id="vue/3/x", tech="vue", version="3", body="# X\n\nvue page.\n"))
    conn = connect(tmp_path / "a.db")
    ix = Indexer(conn, store_with_two_pages, cfg)
    ix.index()
    store_with_two_pages.path_for("vue/3/x").unlink()
    s = ix.index(tech="react")
    assert s.pages_deleted == 0 and s.pages_skipped == 2
    assert ix.index(tech="vue").pages_deleted == 1


def test_portable_copy_reproduces_index(tmp_path, make_page, cfg):
    a, b = tmp_path / "a", tmp_path / "b"
    store_a = build_store(a, make_page, {"memo": BODY_A, "effect": BODY_B})
    shutil.copytree(a / "docs", b / "docs")
    store_b = Store(b / "docs", Manifest(b / "manifest.json"))
    ca, cb = connect(a / "x.db"), connect(b / "x.db")
    Indexer(ca, store_a, cfg).index()
    Indexer(cb, store_b, cfg).index()
    assert chunk_texts(ca) == chunk_texts(cb) != []
