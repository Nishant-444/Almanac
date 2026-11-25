"""Cited, extractive, token-budgeted answer bundle (no generated text)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from almanac.chunker import estimate_tokens
from almanac.query.retrieve import Hit
from almanac.query.understand import Interpretation

MAX_EXCERPTS = 4
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_PARA_BREAK = re.compile(r"\n\n+")


def _measure(b: dict) -> int:
    return estimate_tokens(json.dumps(b, ensure_ascii=False))


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _source_url(h: Hit) -> str:
    return f"{h.url}#{h.anchor}" if h.anchor else h.url


def _assemble(
    i: Interpretation,
    hits: list[Hit],
    *,
    answer_text: str,
    stale_after_days: int,
    now: datetime,
    ms: int,
    query_id: str,
    cut: list[str],
    over_budget: bool = False,
) -> dict:
    sources: list[dict] = []
    ref_of: dict[str, int] = {}
    refs: list[int] = []
    for h in hits:
        url = _source_url(h)
        if url not in ref_of:
            ref_of[url] = len(sources) + 1
            sources.append(
                {
                    "ref": ref_of[url],
                    "id": h.page_id,  # pass to get_page(id=...) for the full page
                    "title": h.title,
                    "kind": h.kind,
                    "trust": h.trust,
                    "version": h.version,
                    "url": url,
                    "fetched_at": h.fetched_at,
                }
            )
        refs.append(ref_of[url])

    warnings = [dict(w) for w in i.warnings]
    cutoff = (now if now.tzinfo else now.replace(tzinfo=UTC)) - timedelta(days=stale_after_days)
    stale = sorted({r for h, r in zip(hits, refs) if _parse_ts(h.fetched_at) < cutoff})
    if stale:
        warnings.append(
            {
                "code": "STALE",
                "message": f"{len(stale)} cited source(s) older than {stale_after_days} days",
                "refs": stale,
            }
        )
    dep = sorted({r for h, r in zip(hits, refs) if "deprecated" in h.flags})
    if dep:
        warnings.append({"code": "DEPRECATED", "message": "A cited page is marked deprecated", "refs": dep})
    if cut:
        msg = "Cut: " + ", ".join(cut)
        if over_budget:
            msg += "; over budget: mandatory parts exceed max_tokens"
        warnings.append({"code": "TRUNCATED", "message": msg})

    bundle = {
        "status": "ok",
        "interpretation": {
            "tech": i.tech,
            "version": i.version,
            "version_source": i.version_source,
            "intent": list(i.intent),
            "summary": i.summary,
        },
        "warnings": warnings,
        "answer": {"text": answer_text, "refs": [refs[0]]},
        "excerpts": [{"heading_path": h.heading_path, "text": h.text, "refs": [r]} for h, r in zip(hits[1:], refs[1:])],
        "sources": sources,
        "meta": {"tokens": 0, "ms": ms, "query_id": query_id},
    }
    for _ in range(4):  # tokens digits can change the size; settle on a fixed point
        t = _measure(bundle)
        if bundle["meta"]["tokens"] == t:
            break
        bundle["meta"]["tokens"] = t
    return bundle


def _open_fence(text: str) -> str | None:
    """Return the marker of a code fence left open at the end of text, else None."""
    marker = None
    for line in text.splitlines():
        if marker is None:
            if line.startswith(("```", "~~~")):
                marker = line[:3]
        elif line.startswith(marker):
            marker = None
    return marker


def _finish(prefix: str, ellipsis: bool) -> str:
    """Close a dangling code fence (G-2: only appended markers, never rewrites)."""
    marker = _open_fence(prefix)
    if marker:
        return prefix + "\n" + marker + " …"
    return prefix + (" …" if ellipsis else "")


def _answer_cuts(text: str):
    """Yield (prefix, ellipsis) cuts of text, longest first; prefixes are verbatim slices."""
    first = _PARA_BREAK.search(text)
    para_end = first.start() if first else len(text)
    for m in reversed(list(_PARA_BREAK.finditer(text))):
        cand = text[: m.start()].rstrip()
        if cand:
            yield cand, False
    for m in reversed(list(_SENTENCE_END.finditer(text, 0, para_end))):
        cand = text[: m.start()].rstrip()
        if cand:
            yield cand, True


def build_bundle(
    i: Interpretation,
    hits: list[Hit],
    *,
    max_tokens: int,
    stale_after_days: int,
    now: datetime,
    ms: int = 0,
) -> dict:
    if not hits:
        raise ValueError("build_bundle requires at least one hit; use not_found()")
    query_id = "q_" + uuid.uuid4().hex[:12]
    kept = hits[: 1 + MAX_EXCERPTS]
    text = hits[0].text

    def make(hs: list[Hit], t: str, cut: list[str], over_budget: bool = False) -> dict:
        return _assemble(
            i, hs, answer_text=t, stale_after_days=stale_after_days, now=now,
            ms=ms, query_id=query_id, cut=cut, over_budget=over_budget,
        )  # fmt: skip

    b = make(kept, text, [])
    if _measure(b) <= max_tokens:
        return b

    # 1. drop excerpts from the end (sources are rebuilt from the kept hits)
    n = len(kept)
    cut = ["excerpts"] if n > 1 else []
    while n > 1:
        n -= 1
        b = make(kept[:n], text, cut)
        if _measure(b) <= max_tokens:
            return b
    cut_excerpts = len(kept) > 1

    # 2. cut the answer
    cut = (["excerpts"] if cut_excerpts else []) + ["answer"]
    cuts = list(_answer_cuts(text))
    # prefer cuts that end before any code fence opens; then close a dangling fence
    for cand, ell in cuts:
        if _open_fence(cand):
            continue
        b = make(kept[:1], cand + (" …" if ell else ""), cut)
        if _measure(b) <= max_tokens:
            return b
    for cand, ell in cuts:
        b = make(kept[:1], _finish(cand, ell), cut)
        if _measure(b) <= max_tokens:
            return b
    # last resort: hard character cut so the answer stays non-empty
    lo, hi, best = 1, len(text), None
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = _finish(text[:mid].rstrip(), True)
        if _measure(make(kept[:1], cand, cut)) <= max_tokens:
            best, lo = cand, mid + 1
        else:
            hi = mid - 1
    if best is not None:
        return make(kept[:1], best, cut)
    # mandatory parts alone exceed the budget: they outrank it (ruling L)
    return make(kept[:1], _finish(text[:1], True), cut, over_budget=True)


def not_found(i: Interpretation, nearby_topics: list[str]) -> dict:
    return {
        "status": "not_found",
        "interpretation": {"tech": i.tech, "version": i.version, "summary": i.summary},
        "message": f"No indexed source answers this for {i.tech_name} {i.version}.",
        "nearby_topics": list(nearby_topics),
        "suggestions": [
            "Try the official term",
            "Check that the source is tracked: almanac status",
        ],
    }


def validate_bundle(b: dict) -> list[str]:
    """Grounding check (G-1): every statement must cite an existing source."""
    out: list[str] = []
    known = {s.get("ref") for s in b.get("sources", [])}

    def check(label: str, refs) -> None:
        if not refs:
            out.append(f"{label}: no refs")
            return
        for r in refs:
            if r not in known:
                out.append(f"{label}: ref {r} not in sources")

    ans = b.get("answer") or {}
    if not ans.get("text"):
        out.append("answer: empty text")
    check("answer", ans.get("refs"))
    for n, e in enumerate(b.get("excerpts", []), 1):
        check(f"excerpt {n}", e.get("refs"))
    return out


def _refs(refs: list[int]) -> str:
    return "".join(f"[{r}]" for r in refs)


def render_markdown(b: dict) -> str:
    lines = [f"**Interpreted as:** {b['interpretation']['summary']}", ""]
    for w in b.get("warnings", []):
        lines.append(f"> ⚠ {w['code']}: {w['message']}")
    if b.get("warnings"):
        lines.append("")
    lines += ["**Answer**", f"{b['answer']['text']} {_refs(b['answer']['refs'])}", ""]
    if b.get("excerpts"):
        lines.append("**More from the docs**")
        for e in b["excerpts"]:
            lines += [f"_{e['heading_path']}_", f"{e['text']} {_refs(e['refs'])}", ""]
    lines.append("**Sources**")
    for s in b["sources"]:
        lines.append(f"[{s['ref']}] {s['title']}, v{s['version']}, fetched {s['fetched_at'][:10]}, {s['url']}")
    return "\n".join(lines) + "\n"
