import hashlib
import json
import os
import socket
from datetime import UTC, datetime

import pytest

from almanac.chunker import heading_anchor
from almanac.engine import Engine
from almanac.errors import ConfigError
from almanac.paths import AlmanacPaths
from almanac.query.retrieve import Retriever


def snapshot(*paths):
    out = {}
    for p in paths:
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())
        for f in files:
            out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


@pytest.fixture
def tmp_root_with_config(tmp_path):
    from conftest import _ALMANAC_YAML

    AlmanacPaths(tmp_path).config.write_text(_ALMANAC_YAML, encoding="utf-8", newline="\n")
    return tmp_path


def last_log(ir):
    return json.loads(ir.paths.query_log.read_text(encoding="utf-8").splitlines()[-1])


def test_search_ok_and_logged(indexed_root):
    r = Engine(indexed_root.root).search_docs("memo props", tech="react")
    assert r["status"] == "ok" and r["sources"]
    line = last_log(indexed_root)
    assert line["outcome"] == "ok" and line["tech"] == "react"
    assert line["query_id"] == r["meta"]["query_id"]
    assert line["at"].endswith("Z")


def test_clock_is_injectable(indexed_root):
    fixed = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    r = Engine(indexed_root.root, clock=lambda: fixed).search_docs("memo props", tech="react")
    assert any(w["code"] == "STALE" for w in r["warnings"])
    assert last_log(indexed_root)["at"] == "2030-01-02T03:04:05Z"


def test_not_found_has_nearby_topics(indexed_root):
    r = Engine(indexed_root.root).search_docs("quantum flux capacitor", tech="react")
    assert r["status"] == "not_found"
    assert isinstance(r["nearby_topics"], list)
    assert last_log(indexed_root)["outcome"] == "not_found"
    assert last_log(indexed_root)["query_id"] is None


def test_not_found_nearby_topics_from_other_versions(indexed_root):
    r = Engine(indexed_root.root).search_docs("wraps component", tech="react", version="19")
    assert r["status"] == "not_found"
    assert r["nearby_topics"] == ["memo"]


def test_errors_are_structured(indexed_root):
    r = Engine(indexed_root.root).search_docs("memo", tech="nope")
    assert r["status"] == "error" and r["code"] == "E_UNKNOWN_TECH"
    assert "Traceback" not in json.dumps(r)
    assert last_log(indexed_root)["outcome"] == "E_UNKNOWN_TECH"


def test_bad_project_path(indexed_root, tmp_path):
    r = Engine(indexed_root.root).search_docs("memo", tech="react", project_path=str(tmp_path / "nope"))
    assert r["code"] == "E_BAD_INPUT"
    ok = Engine(indexed_root.root).search_docs("memo", tech="react", project_path=str(tmp_path))
    assert ok["status"] == "ok"


def test_internal_error_hides_trace(indexed_root, monkeypatch, caplog):
    def boom(self, *a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(Retriever, "search", boom)
    with caplog.at_level("ERROR", logger="almanac.engine"):
        r = Engine(indexed_root.root).search_docs("memo", tech="react")
    assert r["code"] == "E_INTERNAL"
    error_id = r["message"].rsplit("error ", 1)[1].rstrip(")")
    assert len(error_id) == 8
    assert "secret detail" not in json.dumps(r) and "Traceback" not in json.dumps(r)
    assert error_id in caplog.text and "secret detail" in caplog.text
    assert last_log(indexed_root)["outcome"] == "E_INTERNAL"


def test_missing_index(tmp_root_with_config):
    e = Engine(tmp_root_with_config)
    assert e.search_docs("memo", tech="react")["code"] == "E_INDEX_MISSING"
    assert not AlmanacPaths(tmp_root_with_config).db.exists()
    assert not AlmanacPaths(tmp_root_with_config).data.exists()


def test_bad_config_propagates(tmp_path):
    AlmanacPaths(tmp_path).config.write_text("technologies: [", encoding="utf-8")
    with pytest.raises(ConfigError):
        Engine(tmp_path)


def test_read_only_and_offline(indexed_root, monkeypatch):
    before = snapshot(indexed_root.paths.docs, indexed_root.paths.db)

    def no_net(*a, **k):
        raise OSError("net")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    e = Engine(indexed_root.root)
    assert e.search_docs("memo", tech="react")["status"] == "ok"
    assert e.get_page(id="react/19/memo")["status"] == "ok"
    assert e.list_technologies()["status"] == "ok"
    assert snapshot(indexed_root.paths.docs, indexed_root.paths.db) == before


def test_get_page_by_id_and_section(indexed_root):
    e = Engine(indexed_root.root)
    r = e.get_page(id="react/19/memo")
    assert r["status"] == "ok" and "memo" in r["markdown"]
    assert r["id"] == "react/19/memo" and r["version"] == "19" and r["fetched_at"]
    assert r["flags"] == [] and r["title"] == "memo"
    assert not r["markdown"].startswith("---")
    s = e.get_page(id="react/19/memo", section="caveats")
    assert s["markdown"].startswith("## Caveats") and "useMemo caches" not in s["markdown"]
    assert "shallow comparison" in s["markdown"]
    assert e.get_page(id="react/19/memo", section="nope")["code"] == "E_NOT_FOUND"


def test_get_page_by_tech_and_path(indexed_root):
    e = Engine(indexed_root.root)
    assert e.get_page(tech="react", path="memo")["id"] == "react/19/memo"
    assert e.get_page(tech="react", version="18", path="memo")["id"] == "react/18/memo"
    assert e.get_page(tech="react", path="nothing")["code"] == "E_NOT_FOUND"
    assert e.get_page(tech="nope", path="memo")["code"] == "E_UNKNOWN_TECH"
    assert e.get_page()["code"] == "E_BAD_INPUT"


@pytest.mark.parametrize(
    "bad",
    [
        "../almanac",
        "react/19/../../almanac",
        "/etc/passwd",
        "C:\\Windows\\win",
        "react\\19\\memo",
        "react/19/memo.md",
        "react/./memo",
        "react/19/memo\n",
    ],
)
def test_get_page_rejects_escape(indexed_root, bad):
    assert Engine(indexed_root.root).get_page(id=bad)["code"] == "E_BAD_INPUT"


def test_get_page_dot_version_is_bad_input(indexed_root):
    assert Engine(indexed_root.root).get_page(tech="react", version=".", path="memo")["code"] == "E_BAD_INPUT"


def test_engine_usable_from_different_threads(indexed_root):
    import threading

    e = Engine(indexed_root.root)
    assert e.search_docs("memo props", tech="react")["status"] == "ok"  # opens the connection here
    result = {}
    t = threading.Thread(target=lambda: result.update(r=e.search_docs("memo props", tech="react")))
    t.start()
    t.join()
    assert result["r"]["status"] == "ok"


def test_engine_concurrent_threads(indexed_root):
    import threading

    e = Engine(indexed_root.root)
    assert e.search_docs("memo props", tech="react")["status"] == "ok"
    barrier = threading.Barrier(4)
    results = []

    def work():
        barrier.wait()
        results.append(e.search_docs("memo props", tech="react"))

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r["status"] for r in results] == ["ok"] * 4


def _dir_link(target, link) -> None:
    """A directory symlink, or on Windows without the privilege, a junction (needs no privilege)."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlinks unavailable")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_get_page_symlink_escape(indexed_root, tmp_path_factory, make_page):
    from almanac.store import page_to_text

    outside = tmp_path_factory.mktemp("outside")  # a page-shaped file outside docs/
    secret = make_page(id="react/evil/secret", title="secret", body="# Secret\n\nTOP SECRET\n")
    (outside / "secret.md").write_text(page_to_text(secret), encoding="utf-8", newline="\n")
    link = indexed_root.paths.docs / "react" / "evil"
    _dir_link(outside, link)
    assert (link / "secret.md").is_file()  # the link really reaches the outside page
    r = Engine(indexed_root.root).get_page(id="react/evil/secret")
    assert r["code"] == "E_BAD_INPUT" and "TOP SECRET" not in json.dumps(r)


def test_section_excludes_code_fence_headings_and_stops_at_peer(tmp_path):
    from almanac.engine import extract_section

    body = (
        "# T\n\nintro\n\n## A {#custom}\n\ntext\n```\n## not a heading\n```\n\n### Sub\n\nsub text\n\n## B\n\nother\n"
    )
    out = extract_section(body, "custom")
    assert "## not a heading" in out and "### Sub" in out and "sub text" in out and "other" not in out
    assert extract_section(body, "b").startswith("## B")
    assert extract_section(body, "missing") is None


def test_heading_anchor():
    assert heading_anchor("Caveats") == "caveats"
    assert heading_anchor("Use It {#my-id}") == "my-id"


def test_list_technologies(indexed_root):
    r = Engine(indexed_root.root).list_technologies()
    techs = {t["id"]: t for t in r["technologies"]}
    assert techs["react"]["versions_indexed"] == ["18", "19"]
    assert techs["react"]["page_count"] == 5
    assert techs["react"]["name"] == "React"
    assert techs["react"]["last_fetched"].startswith("2026-11-24")
    assert techs["python"]["page_count"] == 1


def test_list_technologies_missing_index(tmp_root_with_config):
    r = Engine(tmp_root_with_config).list_technologies()
    assert r["status"] == "ok"
    assert {t["id"] for t in r["technologies"]} == {"react", "python"}
    assert all(
        t["page_count"] == 0 and t["versions_indexed"] == [] and t["last_fetched"] is None for t in r["technologies"]
    )


def test_strict_raises_as_internal(indexed_root, monkeypatch):
    monkeypatch.setattr("almanac.engine.validate_bundle", lambda b: ["answer: no refs"])
    r = Engine(indexed_root.root, strict=True).search_docs("memo props", tech="react")
    assert r["code"] == "E_INTERNAL"


def test_non_strict_drops_bad_excerpts(indexed_root, monkeypatch):
    import almanac.engine as eng

    real = eng.build_bundle

    def bad(*a, **k):
        b = real(*a, **k)
        b["excerpts"].append({"heading_path": "x", "text": "y", "refs": [99]})
        return b

    monkeypatch.setattr(eng, "build_bundle", bad)
    r = Engine(indexed_root.root).search_docs("memo props", tech="react")
    assert r["status"] == "ok" and all(e["refs"] != [99] for e in r["excerpts"])


# --- final fix wave: bundle ids, get_page defaults, heading markers -------------------------------


def test_source_id_reaches_get_page(indexed_root):
    """F5: every source carries the page id, so get_page is reachable from a search result."""
    e = Engine(indexed_root.root)
    r = e.search_docs("memo props", tech="react")
    assert all("id" in s for s in r["sources"])
    page = e.get_page(id=r["sources"][0]["id"])
    assert page["status"] == "ok" and page["id"] == r["sources"][0]["id"]


_LATEST_FIRST_YAML = """\
technologies:
  - id: widget
    name: Widget
    versions: ["latest", "2"]
    sources:
      - {id: widget-docs, type: llms_txt, url: "https://example.org/llms.txt", kind: reference, trust: 1}
"""


def _widget_root(tmp_path, make_page, body):
    from almanac.db import connect
    from almanac.indexer import Indexer
    from almanac.registry import load_config
    from almanac.store import Manifest, Store

    paths = AlmanacPaths(tmp_path)
    paths.config.write_text(_LATEST_FIRST_YAML, encoding="utf-8", newline="\n")
    store = Store(paths.docs, Manifest(paths.manifest))
    page = make_page(
        id="widget/2/guide", tech="widget", version="2", title="Widget", source_id="widget-docs", body=body
    )
    store.put(page)
    store.manifest.save()
    conn = connect(paths.db)
    Indexer(conn, store, load_config(paths.config)).index()
    conn.close()
    return paths, store


def test_get_page_tech_path_defaults_to_stored_version(tmp_path, make_page):
    """Q10: with tech+path only, use the version the cleaner stores under (first non-latest)."""
    _widget_root(tmp_path, make_page, "# Widget\n\nThe widget renders a list.\n")
    r = Engine(tmp_path).get_page(tech="widget", path="guide")
    assert r["status"] == "ok" and r["id"] == "widget/2/guide"


def test_get_page_fetched_at_from_manifest(tmp_path, make_page):
    """Q10: freshness comes from the manifest (Ruling F), not the front-matter."""
    _, store = _widget_root(tmp_path, make_page, "# Widget\n\nThe widget renders a list.\n")
    store.manifest.touch_url(store.read("widget/2/guide").source_url, datetime(2027, 3, 4, 5, 6, 7, tzinfo=UTC))
    store.manifest.save()
    assert Engine(tmp_path).get_page(id="widget/2/guide")["fetched_at"] == "2027-03-04T05:06:07Z"


def test_answer_text_has_no_heading_id_marker(tmp_path, make_page):
    """Q14: the stored {#id} marker feeds anchors but never reaches answer or excerpt text."""
    body = "# Widget {#top}\n\nThe widget renders a quokka list.\n\n## Quokka options {#opts}\n\nquokka sizes vary.\n"
    _widget_root(tmp_path, make_page, body)
    r = Engine(tmp_path).search_docs("quokka", tech="widget")
    assert r["status"] == "ok"
    texts = [r["answer"]["text"], *(x["text"] for x in r["excerpts"])]
    assert all("{#" not in t for t in texts), texts
    assert any(s["url"].endswith("#opts") or s["url"].endswith("#top") for s in r["sources"])
