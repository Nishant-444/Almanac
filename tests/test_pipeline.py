from __future__ import annotations

import functools
import json
import sqlite3
import subprocess

import pytest
from conftest import write_config

from almanac import pipeline
from almanac.engine import Engine
from almanac.fetch.git import GitFetcher
from almanac.models import ChangeKind
from almanac.pipeline import run_fetch, run_index

DEAD = {"id": "dead", "type": "url_list", "urls": ["http://127.0.0.1:9/x"], "kind": "guide", "trust": 1}
FEED = {"id": "rel", "type": "feed", "url": "http://127.0.0.1:9/r.atom", "kind": "changelog", "trust": 1}


def nosleep(_s):
    pass


def test_fetch_isolates_failures(docs_site, tmp_root):
    write_config(tmp_root, docs_site, extra_sources=[DEAD, FEED])
    rep = run_fetch(tmp_root, sleep=nosleep)
    assert "react-docs" in rep.ok and "dead" in rep.failed
    assert rep.skipped["rel"] == "not supported until v1.0"
    assert rep.changes[ChangeKind.new] == 3
    assert len(list((tmp_root / "docs").rglob("*.md"))) == 3


def test_second_run_unchanged_skips(docs_site, tmp_root):
    write_config(tmp_root, docs_site)
    run_fetch(tmp_root, sleep=nosleep)
    rep = run_fetch(tmp_root, sleep=nosleep)
    assert rep.ok == ["react-docs"] and sum(rep.changes.values()) == 0


def test_clean_failure_isolated_and_refetched(docs_site, tmp_root, monkeypatch):
    other = {"id": "other", "type": "url_list", "urls": [f"{docs_site}/guides/perf.html"], "kind": "guide", "trust": 2}
    write_config(tmp_root, docs_site, extra_sources=[other])
    real = pipeline.clean

    def boom(raw, source, *a, **kw):
        if source.id == "react-docs":
            raise RuntimeError("clean exploded")
        return real(raw, source, *a, **kw)

    monkeypatch.setattr(pipeline, "clean", boom)
    rep = run_fetch(tmp_root, sleep=nosleep)
    assert rep.failed == {"react-docs": "clean exploded"} and rep.ok == ["other"]

    monkeypatch.setattr(pipeline, "clean", real)
    rep = run_fetch(tmp_root, sleep=nosleep)
    assert rep.ok == ["react-docs", "other"] and rep.changes[ChangeKind.new] == 3


def test_dry_run_and_selection(tmp_root):
    write_config(tmp_root, "http://127.0.0.1:9", extra_sources=[FEED])
    rep = run_fetch(tmp_root, dry_run=True)
    assert rep.ok == ["react-docs"] and "rel" in rep.skipped
    with pytest.raises(pipeline.SelectionError):
        run_fetch(tmp_root, source="nope", dry_run=True)
    with pytest.raises(pipeline.SelectionError):
        run_fetch(tmp_root, tech="nope", dry_run=True)


def test_run_index(docs_site, tmp_root):
    write_config(tmp_root, docs_site)
    run_fetch(tmp_root, sleep=nosleep)
    assert run_index(tmp_root).pages_indexed >= 2
    assert run_index(tmp_root).pages_indexed == 0


def test_disabled_source_never_touched(docs_site, tmp_root):
    off = {
        "id": "off",
        "type": "url_list",
        "urls": ["http://127.0.0.1:9/x"],
        "kind": "guide",
        "trust": 1,
        "enabled": False,
    }
    write_config(tmp_root, docs_site, extra_sources=[off])
    rep = run_fetch(tmp_root, sleep=nosleep)
    assert "off" not in rep.ok and "off" not in rep.failed and "off" not in rep.skipped
    assert rep.ok == ["react-docs"]
    conn = sqlite3.connect(tmp_root / "data" / "almanac.db")
    try:
        assert conn.execute("select count(*) from fetch_log where url like ?", ("%127.0.0.1:9%",)).fetchone()[0] == 0
    finally:
        conn.close()
    with pytest.raises(pipeline.SelectionError):
        run_fetch(tmp_root, source="off", dry_run=True)


# --- final fix wave: page ids, freshness and metadata -------------------------------------------

OLD = "2020-01-01T00:00:00Z"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "docs").mkdir(parents=True)
    _git(r, "init", "-q")
    _git(r, "config", "user.name", "T")
    _git(r, "config", "user.email", "t@example.org")
    (r / "docs" / "hooks.md").write_bytes(b"# Hooks\n\nHooks let you use state.\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one")
    return r


def _write_git_config(root, url):
    config = {
        "settings": {"allow_private_network": True},
        "technologies": [
            {
                "id": "react",
                "name": "React",
                "versions": ["19"],
                "sources": [{"id": "react-git", "type": "git", "url": url, "kind": "reference", "trust": 1}],
            }
        ],
    }
    (root / "almanac.yaml").write_text(json.dumps(config), encoding="utf-8", newline="\n")


def _manifest(root):
    return json.loads((root / "docs" / "manifest.json").read_text(encoding="utf-8"))


def _age_manifest(root):
    path = root / "docs" / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["pages"].values():
        entry["fetched_at"] = OLD
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def _db_fetched(root):
    conn = sqlite3.connect(root / "data" / "almanac.db")
    try:
        return dict(conn.execute("select page_key, fetched_at from page"))
    finally:
        conn.close()


def test_git_page_id_stable_across_commits(repo, tmp_path, monkeypatch):
    """F1: a GitHub source keeps one page per file; the sha lives only in the citation URL."""
    gh = "https://github.com/facebook/react"
    local = repo.as_uri()

    def run_git(cmd, **kw):
        return subprocess.run([local if c == gh else c for c in cmd], check=kw.pop("check", True), **kw)

    monkeypatch.setattr("almanac.fetch.git.check_url", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "GitFetcher", functools.partial(GitFetcher, run_git=run_git))
    root = tmp_path / "proj"
    root.mkdir()
    _write_git_config(root, gh)
    assert run_fetch(root).ok == ["react-git"]
    first = _manifest(root)["pages"]
    (repo / "docs" / "hooks.md").write_bytes(b"# Hooks\n\nHooks let you use state and effects.\n")
    _git(repo, "commit", "-q", "-am", "two")
    rep = run_fetch(root)
    assert rep.ok == ["react-git"] and rep.changes[ChangeKind.changed] == 1
    second = _manifest(root)["pages"]
    assert list(first) == list(second) == ["react/19/facebook-react-docs-hooks"]
    assert len(list((root / "docs").rglob("*.md"))) == 1
    sha = _git(repo, "rev-parse", "HEAD")
    assert second["react/19/facebook-react-docs-hooks"]["source_url"] == f"{gh}/blob/{sha}/docs/hooks.md"


def test_304_refreshes_manifest_and_db_fetched_at(docs_site, tmp_root):
    """F2 (web): a 304 is a successful re-check, so fetched_at moves forward."""
    write_config(tmp_root, docs_site)
    run_fetch(tmp_root, sleep=nosleep)
    run_index(tmp_root)
    _age_manifest(tmp_root)
    run_fetch(tmp_root, sleep=nosleep)
    conn = sqlite3.connect(tmp_root / "data" / "almanac.db")
    try:
        assert conn.execute("select count(*) from fetch_log where status = 304").fetchone()[0] >= 3
    finally:
        conn.close()
    pages = _manifest(tmp_root)["pages"]
    assert pages and all(e["fetched_at"] > OLD for e in pages.values())
    run_index(tmp_root)
    assert _db_fetched(tmp_root) == {k: e["fetched_at"] for k, e in pages.items()}


def test_unchanged_git_sha_refreshes_fetched_at(repo, tmp_path):
    """F2 (git): an unchanged sha is a successful re-check too."""
    root = tmp_path / "proj"
    root.mkdir()
    _write_git_config(root, repo.as_uri())
    run_fetch(root)
    run_index(root)
    _age_manifest(root)
    rep = run_fetch(root)
    assert rep.ok == ["react-git"] and sum(rep.changes.values()) == 0  # sha unchanged: nothing yielded
    pages = _manifest(root)["pages"]
    assert pages and all(e["fetched_at"] > OLD for e in pages.values())
    run_index(root)
    assert _db_fetched(root) == {k: e["fetched_at"] for k, e in pages.items()}


def test_trust_change_reaches_page_and_ranking(docs_site, tmp_root):
    """F6: a config edit to trust rewrites the front-matter and re-indexes, so ranking follows it."""

    def config(trust_a):
        src = lambda sid, url, trust: {"id": sid, "type": "url_list", "urls": [url], "kind": "guide", "trust": trust}
        cfg = {
            "settings": {"allow_private_network": True, "rate_limit_per_host_rps": 100},
            "technologies": [
                {
                    "id": "react",
                    "name": "React",
                    "versions": ["19"],
                    "sources": [
                        src("a", f"{docs_site}/reference/memo.html", trust_a),
                        src("b", f"{docs_site}/reference/memo.html?copy=1", 2),
                    ],
                }
            ],
        }
        (tmp_root / "almanac.yaml").write_text(json.dumps(cfg), encoding="utf-8", newline="\n")

    def top_source():
        return Engine(tmp_root).search_docs("skip re-rendering memo", tech="react")["sources"][0]

    config(1)
    run_fetch(tmp_root, sleep=nosleep)
    run_index(tmp_root)
    assert top_source()["trust"] == 1
    config(3)
    rep = run_fetch(tmp_root, force=True, sleep=nosleep)
    assert rep.changes[ChangeKind.changed] == 1
    run_index(tmp_root)
    conn = sqlite3.connect(tmp_root / "data" / "almanac.db")
    try:
        trusts = dict(conn.execute("select source_id, trust from page"))
    finally:
        conn.close()
    assert trusts == {"a": 3, "b": 2}
    top = top_source()
    assert top["trust"] == 2 and "?copy=1" in top["url"]


def test_run_index_resolves_tech(docs_site, tmp_root):
    """Q16: run_index resolves tech like run_fetch: aliases/names work, unknown values raise SelectionError."""
    write_config(tmp_root, docs_site)
    run_fetch(tmp_root, sleep=nosleep)
    assert run_index(tmp_root, tech="React").pages_indexed == 3  # name, case-insensitive
    with pytest.raises(pipeline.SelectionError):
        run_index(tmp_root, tech="../x")
