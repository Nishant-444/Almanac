import sqlite3

import pytest

from almanac.db import connect
from almanac.errors import AlmanacError
from almanac.query.retrieve import Retriever
from almanac.query.understand import understand


def retriever(root):
    return Retriever(connect(root.db, readonly=True))


def test_indexed_versions(indexed_root):
    assert retriever(indexed_root).indexed_versions() == {"react": ["18", "19"], "python": ["3.12"]}


def test_retrieve_ranks_and_filters(indexed_root, cfg):
    r = retriever(indexed_root)
    hits = r.search(understand("memo props", cfg, r.indexed_versions(), tech="react"))
    assert hits[0].page_id == "react/19/memo" and all(h.version == "19" for h in hits)
    assert hits[0].flags == [] and hits[0].fetched_at.endswith("Z") and hits[0].score > 0
    both = r.search(understand("memo", cfg, r.indexed_versions(), tech="react"), any_version=True)
    assert {h.version for h in both} == {"18", "19"}


def test_flags_parsed(indexed_root, cfg):
    r = retriever(indexed_root)
    hits = r.search(understand("findDOMNode", cfg, r.indexed_versions(), tech="react"))
    assert hits[0].flags == ["deprecated"]


def test_trust_breaks_ties(indexed_root, cfg):
    r = retriever(indexed_root)
    hits = r.search(understand("quokka", cfg, r.indexed_versions(), tech="react"))
    assert [h.page_id for h in hits[:2]] == ["react/19/tie-trusted", "react/19/tie-blog"]
    assert hits[0].score > hits[1].score


def test_removed_pages_excluded(indexed_root, cfg):
    conn = connect(indexed_root.db)
    conn.execute("UPDATE page SET status = 'removed' WHERE page_key = 'react/19/memo'")
    conn.commit()
    conn.close()
    r = retriever(indexed_root)
    hits = r.search(understand("memo props", cfg, r.indexed_versions(), tech="react"))
    assert "react/19/memo" not in {h.page_id for h in hits}


def test_index_missing(tmp_path, cfg):
    conn = connect(tmp_path / "empty.db")
    with pytest.raises(AlmanacError) as e:
        Retriever(conn).indexed_versions()
    assert e.value.code == "E_INDEX_MISSING"
    bare = sqlite3.connect(tmp_path / "bare.db")  # no schema at all
    with pytest.raises(AlmanacError) as e:
        Retriever(bare).indexed_versions()
    assert e.value.code == "E_INDEX_MISSING"


def test_no_query_tokens_returns_empty(indexed_root, cfg):
    r = retriever(indexed_root)
    assert r.search(understand("the and of", cfg, r.indexed_versions(), tech="react")) == []


@pytest.mark.parametrize(
    "q", ['"unterminated', "memo*", "-flag", "a:b", "(memo", "NEAR AND OR NOT", "the and of", "😀 ü"]
)
def test_fts_syntax_never_crashes(indexed_root, cfg, q):
    r = retriever(indexed_root)
    assert isinstance(r.search(understand(q, cfg, r.indexed_versions(), tech="react")), list)
