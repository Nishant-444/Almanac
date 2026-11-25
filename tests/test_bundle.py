import json
from datetime import UTC, datetime, timedelta

import pytest

from almanac.chunker import estimate_tokens
from almanac.query.retrieve import Hit
from almanac.query.understand import Interpretation
from almanac.response.bundle import build_bundle, not_found, render_markdown, validate_bundle

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hit(n, text="Use the router to define routes.", **kw):
    base = {
        "chunk_id": n,
        "page_id": f"p{n}",
        "title": f"Page {n}",
        "heading_path": f"Guide > Section {n}",
        "anchor": f"s{n}",
        "text": text,
        "url": f"https://docs.example.com/p{n}",
        "version": "2.0",
        "kind": "guide",
        "trust": 1,
        "fetched_at": _iso(NOW - timedelta(days=1)),
        "flags": [],
        "score": 1.0,
    }
    base.update(kw)
    return Hit(**base)


@pytest.fixture
def interp():
    return Interpretation(
        tech="demo",
        tech_name="Demo",
        version="2.0",
        version_source="explicit",
        intent=["howto"],
        summary="Demo 2.0 how-to: routing",
        fts_query="routing",
        warnings=[{"code": "VERSION_ASSUMED", "message": "assumed"}],
    )


@pytest.fixture
def hits():
    return [_hit(i) for i in range(1, 4)]


@pytest.fixture
def big_hits():
    para = "Sentence one is here. Sentence two follows it. " * 20
    return [_hit(i, text="\n\n".join([para] * 6)) for i in range(1, 6)]


def test_bundle_is_extractive_and_cited(interp, hits):
    b = build_bundle(interp, hits, max_tokens=3000, stale_after_days=14, now=NOW)
    assert b["status"] == "ok" and b["answer"]["text"] == hits[0].text
    assert b["answer"]["refs"] == [1]
    assert validate_bundle(b) == []
    assert b["sources"][0]["url"].endswith("#" + hits[0].anchor)
    assert b["sources"][0]["fetched_at"]
    assert [e["refs"] for e in b["excerpts"]] == [[2], [3]]
    assert b["warnings"][0]["code"] == "VERSION_ASSUMED"
    assert b["meta"]["query_id"].startswith("q_")
    assert b["meta"]["tokens"] == estimate_tokens(json.dumps(b, ensure_ascii=False))


def test_stale_and_deprecated_warnings(interp, hits):
    hits[1] = _hit(2, fetched_at=_iso(NOW - timedelta(days=20)), flags=["deprecated"])
    b = build_bundle(interp, hits, max_tokens=3000, stale_after_days=14, now=NOW)
    codes = [w["code"] for w in b["warnings"]]
    assert codes == ["VERSION_ASSUMED", "STALE", "DEPRECATED"]
    stale = b["warnings"][1]
    assert stale["refs"] == [2] and stale["message"] == "1 cited source(s) older than 14 days"
    assert b["warnings"][2]["refs"] == [2]
    assert b["warnings"][2]["message"] == "A cited page is marked deprecated"


def test_dedupe_and_plain_url(interp):
    hs = [_hit(1), _hit(2, url="https://docs.example.com/p1", anchor="s1"), _hit(3, anchor="")]
    b = build_bundle(interp, hs, max_tokens=3000, stale_after_days=14, now=NOW)
    assert len(b["sources"]) == 2
    assert b["excerpts"][0]["refs"] == [1]
    assert b["sources"][1]["url"] == "https://docs.example.com/p3"


def test_empty_hits_raises(interp):
    with pytest.raises(ValueError):
        build_bundle(interp, [], max_tokens=3000, stale_after_days=14, now=NOW)


def test_budget_trims_excerpts_then_answer(interp, big_hits):
    b = build_bundle(interp, big_hits, max_tokens=500, stale_after_days=14, now=NOW)
    assert estimate_tokens(json.dumps(b, ensure_ascii=False)) <= 500 and b["answer"]["text"]
    t = [w for w in b["warnings"] if w["code"] == "TRUNCATED"]
    assert t and t[0]["message"] == "Cut: excerpts, answer"
    assert b["excerpts"] == [] and len(b["sources"]) == 1
    assert big_hits[0].text.startswith(b["answer"]["text"].removesuffix(" …").rstrip())
    assert validate_bundle(b) == []


def test_budget_trims_only_excerpts(interp, big_hits):
    full = build_bundle(interp, big_hits, max_tokens=100000, stale_after_days=14, now=NOW)
    assert not any(w["code"] == "TRUNCATED" for w in full["warnings"])
    one = len(json.dumps(full, ensure_ascii=False)) // 4
    b = build_bundle(interp, big_hits, max_tokens=one - 200, stale_after_days=14, now=NOW)
    t = [w for w in b["warnings"] if w["code"] == "TRUNCATED"]
    assert t[0]["message"] == "Cut: excerpts"
    assert b["answer"]["text"] == big_hits[0].text
    assert len(b["excerpts"]) < 4
    assert {s["ref"] for s in b["sources"]} == {1, *(r for e in b["excerpts"] for r in e["refs"])}


def test_single_paragraph_cut_at_sentence(interp):
    h = [_hit(1, text="Alpha is first. " * 200)]
    b = build_bundle(interp, h, max_tokens=300, stale_after_days=14, now=NOW)
    assert b["answer"]["text"].endswith("first. …")
    assert estimate_tokens(json.dumps(b, ensure_ascii=False)) <= 300


def test_non_ascii_not_overcounted(interp):
    h = [_hit(1, text="日本語のドキュメント。" * 20)]
    b = build_bundle(interp, h, max_tokens=3000, stale_after_days=14, now=NOW)
    assert b["meta"]["tokens"] == estimate_tokens(json.dumps(b, ensure_ascii=False))


def test_validator_rejects_uncited():
    assert validate_bundle({"answer": {"text": "x", "refs": [9]}, "excerpts": [], "sources": []})
    assert validate_bundle({"answer": {"text": "x", "refs": []}, "excerpts": [], "sources": []})
    src = [{"ref": 1}]
    assert validate_bundle({"answer": {"text": "", "refs": [1]}, "excerpts": [], "sources": src})
    bad = {
        "answer": {"text": "x", "refs": [1]},
        "sources": src,
        "excerpts": [{"heading_path": "h", "text": "t", "refs": []}],
    }
    assert validate_bundle(bad)


def test_not_found_shape(interp):
    nf = not_found(interp, ["Routing", "Auth"])
    assert set(nf) == {"status", "interpretation", "message", "nearby_topics", "suggestions"}
    assert nf["status"] == "not_found"
    assert nf["interpretation"] == {"tech": "demo", "version": "2.0", "summary": interp.summary}
    assert nf["message"] == "No indexed source answers this for Demo 2.0."
    assert nf["nearby_topics"] == ["Routing", "Auth"]
    assert nf["suggestions"] == [
        "Try the official term",
        "Check that the source is tracked: almanac status",
    ]


def test_render_markdown_order(interp, hits):
    hits[1] = _hit(2, fetched_at=_iso(NOW - timedelta(days=20)))
    b = build_bundle(interp, hits, max_tokens=3000, stale_after_days=14, now=NOW)
    md = render_markdown(b)
    assert md.startswith("**Interpreted as:** Demo 2.0 how-to: routing")
    assert "> ⚠ VERSION_ASSUMED: assumed" in md
    assert "> ⚠ STALE:" in md
    order = [md.index(k) for k in ("**Answer**", "**More from the docs**", "**Sources**")]
    assert order == sorted(order)
    assert hits[0].text + " [1]" in md
    assert "_Guide > Section 2_\n" + hits[1].text + " [2]" in md
    assert "[1] Page 1, v2.0, fetched 2026-11-23, https://docs.example.com/p1#s1" in md


def _fences(md):
    return sum(1 for ln in md.splitlines() if ln.startswith(("```", "~~~")))


def _tight(interp, text):
    """Budget just below the full bundle so the answer must be cut."""
    full = build_bundle(interp, [_hit(1, text=text)], max_tokens=10**6, stale_after_days=14, now=NOW)
    return full["meta"]["tokens"]


def test_sentence_cut_is_verbatim_prefix(interp):
    text = "Intro line one. Second sentence here.\n  - item a\n  - item b. " + "More words here. " * 80
    h = [_hit(1, text=text)]
    b = build_bundle(interp, h, max_tokens=_tight(interp, text) // 2, stale_after_days=14, now=NOW)
    kept = b["answer"]["text"].removesuffix(" …")
    assert b["answer"]["text"].endswith(" …") and text.startswith(kept)
    assert "\n  - item a" in kept


def test_paragraph_cut_is_verbatim(interp):
    paras = ["Para one.\n  - a\n  - b", "Para two " * 40, "Para three " * 40]
    text = "\n\n".join(paras)
    b = build_bundle(
        interp, [_hit(1, text=text)], max_tokens=_tight(interp, text) - 150,
        stale_after_days=14, now=NOW,
    )  # fmt: skip
    assert text.startswith(b["answer"]["text"]) and b["answer"]["text"] != text


def test_over_budget_mandatory_parts_win(interp):
    h = [_hit(1, title="T" * 4000, url="https://docs.example.com/" + "u" * 4000)]
    b = build_bundle(interp, h, max_tokens=500, stale_after_days=14, now=NOW)
    assert b["answer"]["text"] and validate_bundle(b) == []
    t = next(w for w in b["warnings"] if w["code"] == "TRUNCATED")
    assert t["message"].endswith("; over budget: mandatory parts exceed max_tokens")


def test_cut_backs_off_before_open_fence(interp):
    head = "Intro paragraph.\n\n```py\nx = 1\n\ny = 2\n```"
    text = head + "\n\n" + "Tail words here. " * 80
    b = build_bundle(
        interp, [_hit(1, text=text)], max_tokens=_tight(interp, head) + 9,
        stale_after_days=14, now=NOW,
    )  # fmt: skip
    assert b["answer"]["text"] == "Intro paragraph."
    assert _fences(render_markdown(b)) % 2 == 0


def test_cut_closes_fence_when_answer_starts_with_one(interp):
    text = "```py\n" + "x = 1\n\n" * 200 + "```"
    b = build_bundle(
        interp, [_hit(1, text=text)], max_tokens=_tight(interp, text) // 2,
        stale_after_days=14, now=NOW,
    )  # fmt: skip
    a = b["answer"]["text"]
    assert a.startswith("```py") and a.endswith("\n``` …")
    assert _fences(render_markdown(b)) % 2 == 0
