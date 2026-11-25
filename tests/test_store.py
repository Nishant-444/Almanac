from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

from almanac.models import ChangeKind
from almanac.store import Manifest, Store, content_hash, page_from_text, page_to_text, slugify_url


def test_slugify_url():
    assert slugify_url("https://react.dev/reference/react/useMemo") == "reference-react-usememo"
    assert slugify_url("https://x.dev/docs/Intro.HTML") == "docs-intro"
    assert slugify_url("https://x.dev/") == "index"
    assert len(slugify_url("https://x.dev/" + "a/" * 200)) <= 120


def test_put_new_then_unchanged_then_changed(tmp_path, make_page):
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "docs/manifest.json"))
    p = make_page(id="react/19/memo", body="# memo\nText")
    assert store.put(p) is ChangeKind.new
    assert (tmp_path / "docs/react/19/memo.md").exists()
    assert store.put(p) is ChangeKind.unchanged
    assert store.put(replace(p, body="# memo\nNew")) is ChangeKind.changed
    assert store.manifest.page_entry("react/19/memo")["content_hash"] == content_hash("# memo\nNew")


def test_unchanged_updates_manifest_fetched_at_only(tmp_path, make_page):
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    p = make_page()
    store.put(p)
    on_disk = (tmp_path / "docs/react/19/memo.md").read_bytes()
    later = p.fetched_at + timedelta(days=1)
    assert store.put(replace(p, fetched_at=later)) is ChangeKind.unchanged
    assert (tmp_path / "docs/react/19/memo.md").read_bytes() == on_disk
    assert store.manifest.page_entry(p.id)["fetched_at"] == "2026-11-25T06:10:00Z"


def test_unicode_round_trip_is_byte_identical(tmp_path, make_page):
    body = "# Pfeile → ü 日本\n\n```py\nprint('日本')\n```\n"
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    store.put(make_page(id="t/1/u", title="Ü → 日本", body=body))
    raw = (tmp_path / "docs/t/1/u.md").read_bytes()
    assert b"\r\n" not in raw and "日本".encode() in raw
    assert store.read("t/1/u").body == body and store.read("t/1/u").title == "Ü → 日本"


def test_crlf_and_edge_whitespace_in_body_survive(tmp_path, make_page):
    body = "\n\nline1\r\nline2  \n\n"
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    store.put(make_page(body=body))
    assert store.read("react/19/memo").body == body


def test_slug_collision_gets_hash_suffix(tmp_path):
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    # make_page is not needed: record directly through put below
    from datetime import UTC, datetime

    from almanac.models import Kind, Page

    def put(url: str) -> str:
        pid = store.make_page_id("t", "1", url)
        store.put(
            Page(
                id=pid, tech="t", version="1", kind=Kind.guide, trust=2, title="x", source_url=url,
                source_id="s", fetched_at=datetime(2026, 1, 1, tzinfo=UTC), content_hash="", body="b",
            )
        )  # fmt: skip
        return pid

    first = put("https://x.dev/a/b")
    second = put("https://x.dev/a-b")
    assert first == "t/1/a-b"
    assert second != first and second.startswith("t/1/a-b-") and len(second.rsplit("-", 1)[1]) == 8
    assert put("https://x.dev/a-b") == second  # known URL keeps its id
    assert store.make_page_id("t", "1", "https://x.dev/a/b") == first


def test_path_for_rejects_traversal(tmp_path):
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    for bad in ("../x", "a/../../x", "/abs", "a//b"):
        try:
            store.path_for(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_manifest_save_is_atomic_and_reloads(tmp_path, make_page):
    path = tmp_path / "sub/manifest.json"
    m = Manifest.load(path)
    assert m.http_validators("https://x") == (None, None)
    m.set_http_validators("https://x", '"abc"', "Wed, 01 Jan 2026 00:00:00 GMT")
    m.set_source_state("s1", last_run="2026-11-24", count=3)
    m.set_source_state("s1", count=4)
    p = make_page()
    m.record_page(p, path.parent / "p.md")
    m.save()
    assert not list(path.parent.glob("*.tmp"))
    again = Manifest.load(path)
    assert again.http_validators("https://x") == ('"abc"', "Wed, 01 Jan 2026 00:00:00 GMT")
    assert again.source_state("s1") == {"last_run": "2026-11-24", "count": 4}
    assert again.source_state("nope") == {}
    assert again.page_id_for_url("react", "19", p.source_url) == p.id
    assert again.page_id_for_url("react", "18", p.source_url) is None


def test_page_text_round_trip(make_page):
    p = make_page(
        last_modified="2026-10-30", flags=["deprecated"], description="d: colon", canonical_url="https://c/x",
        licence="MIT", extra={"k": "v"}, version="19", title="yes", body="x\n",
    )  # fmt: skip
    text = page_to_text(p)
    assert text.startswith("---\nid: react/19/memo\ntech: react\nversion: '19'\nkind: reference\n")
    assert "fetched_at: '2026-11-24T06:10:00Z'" in text or "fetched_at: 2026-11-24T06:10:00Z" in text
    back = page_from_text(text)
    assert back == p
    assert back.fetched_at.tzinfo is UTC or back.fetched_at.utcoffset() == timedelta(0)
    # minimal page with defaults
    q = make_page()
    assert page_from_text(page_to_text(q)) == q


def test_non_utc_fetched_at_is_normalised(make_page):
    p = make_page(fetched_at=datetime(2026, 11, 24, 8, 10, tzinfo=timezone(timedelta(hours=2))))
    assert page_from_text(page_to_text(p)).fetched_at == datetime(2026, 11, 24, 6, 10, tzinfo=UTC)


def test_iter_pages_filters_by_tech(tmp_path, make_page):
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "docs/manifest.json"))
    store.put(make_page(id="react/19/a"))
    store.put(make_page(id="vue/3/b", tech="vue", version="3"))
    assert [p.id for p in store.iter_pages()] == ["react/19/a", "vue/3/b"]
    assert [p.id for p in store.iter_pages("vue")] == ["vue/3/b"]
    assert list(store.iter_pages("none")) == []


def test_metadata_change_rewrites_page(tmp_path, make_page):
    """F6: same body, different trust/kind/title -> changed and rewritten; fetched_at alone is not a change."""
    from almanac.models import Kind

    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    p = make_page()
    store.put(p)
    assert store.put(replace(p, fetched_at=p.fetched_at + timedelta(days=3))) is ChangeKind.unchanged
    for changed in (replace(p, trust=3), replace(p, kind=Kind.guide), replace(p, title="New"), replace(p, flags=["x"])):
        assert store.put(changed) is ChangeKind.changed
        assert store.read(p.id) == replace(changed, content_hash=content_hash(p.body))


def test_iter_pages_skips_unparseable_with_warning(tmp_path, make_page, caplog):
    """M7: broken or front-matter-less files are skipped; a broken page is named in a warning."""
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    store.put(make_page())
    (tmp_path / "docs" / "README.md").write_text("# Readme\n", encoding="utf-8")
    (tmp_path / "docs" / "react" / "19" / "bad.md").write_text("---\nid: [x\n---\n", encoding="utf-8")
    with caplog.at_level("INFO", logger="almanac.store"):
        assert [p.id for p in store.iter_pages()] == ["react/19/memo"]
    assert any("bad.md" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


def test_put_is_atomic(tmp_path, make_page, monkeypatch):
    """M7: a failed write never leaves a truncated page behind."""
    import pytest

    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    p = make_page()
    store.put(p)
    before = (tmp_path / "docs/react/19/memo.md").read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("almanac.store.os.replace", boom)
    with pytest.raises(OSError):
        store.put(replace(p, body="# New\n\n" + "x" * 1000))
    assert (tmp_path / "docs/react/19/memo.md").read_bytes() == before
    assert [x.name for x in (tmp_path / "docs/react/19").iterdir()] == ["memo.md"]
