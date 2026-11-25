from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from almanac.errors import AlmanacError
from almanac.models import Config, Technology

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "not",
        "near",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "how",
        "do",
        "does",
        "did",
        "i",
        "my",
        "me",
        "we",
        "is",
        "are",
        "was",
        "what",
        "why",
        "when",
        "where",
        "which",
        "can",
        "could",
        "should",
        "would",
        "this",
        "that",
        "it",
        "its",
        "be",
        "from",
        "by",
        "as",
        "at",
        "into",
        "get",
        "make",
        "use",
        "using",
    }
)
_TOKEN_RE = re.compile(r"\w+")
_NUM_RE = re.compile(r"\d+")


@dataclass
class Interpretation:
    tech: str
    tech_name: str
    version: str
    version_source: str
    intent: list[str] = field(default_factory=lambda: ["howto"])
    summary: str = ""
    fts_query: str | None = None
    warnings: list[dict] = field(default_factory=list)


def _tuple(v: str) -> tuple[int, ...] | None:
    v = v.removesuffix(".x")
    if not re.fullmatch(r"\d+(\.\d+)*", v):
        return None
    return tuple(int(p) for p in v.split("."))


def map_version(requested: str, indexed: list[str]) -> tuple[str | None, bool]:
    """Choose an indexed version for ``requested``; the bool says whether it is a fallback."""
    if requested in indexed:
        return requested, False
    numeric = [(t, v) for v in indexed if (t := _tuple(v)) is not None]
    if requested == "latest":
        if "latest" in indexed:
            return "latest", False
        return (max(numeric)[1], False) if numeric else (None, False)
    req = _tuple(requested)
    if req is None:
        return None, False
    prefixes = [(t, v) for t, v in numeric if req[: len(t)] == t]
    if prefixes:
        return max(prefixes, key=lambda tv: len(tv[0]))[1], False
    older = [(t, v) for t, v in numeric if t[0] == req[0] and t < req]
    if older:
        return max(older)[1], True
    newer = [(t, v) for t, v in numeric if t[0] == req[0] and t > req]
    if newer:
        return min(newer)[1], True
    return None, False


def build_fts_query(text: str, drop: set[str] = frozenset()) -> str | None:
    tokens: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if tok in STOPWORDS or tok in drop or tok in tokens:
            continue
        tokens.append(tok)
    return " OR ".join(f'"{t}"' for t in tokens) or None


def _names(t: Technology) -> list[str]:
    return [t.id, t.name, *t.aliases]


def _word_re(names: list[str]) -> str:
    alts = "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True))
    return alts


def _detect_tech(query: str, config: Config, tech: str | None) -> Technology:
    if tech is not None:
        found = config.tech(tech)
        if found is None:
            known = [n for t in config.technologies for n in (t.id, *t.aliases)]
            raise AlmanacError(
                "E_UNKNOWN_TECH",
                f"Unknown technology: {tech}",
                {"closest": difflib.get_close_matches(tech, known, n=3)},
            )
        return found
    matches = [
        t
        for t in config.technologies
        if re.search(rf"(?<![\w.-])(?:{_word_re(_names(t))})(?![\w-])", query, re.IGNORECASE)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches and len(config.technologies) == 1:
        return config.technologies[0]
    candidates = matches or config.technologies
    raise AlmanacError(
        "E_AMBIGUOUS",
        "Could not tell which technology you mean; name one explicitly",
        {"interpretations": [{"tech": t.id, "example": f"{t.name}: {query}"} for t in candidates]},
    )


def understand(
    query: str,
    config: Config,
    indexed: dict[str, list[str]],
    *,
    tech: str | None = None,
    version: str | None = None,
) -> Interpretation:
    query = " ".join(query.split())
    t = _detect_tech(query, config, tech)
    available = indexed.get(t.id) or []
    if not available:
        raise AlmanacError(
            "E_INDEX_MISSING",
            f"No pages indexed for {t.name}; run: almanac fetch {t.id} && almanac index",
        )
    warnings: list[dict] = []
    if version:
        requested, source = version, "argument"
    else:
        m = re.search(rf"(?<![\w.-])(?:{_word_re(_names(t))})[ @](\d+(?:\.\d+)*)", query, re.IGNORECASE)
        if m:
            requested, source = m.group(1), "query"
        else:
            requested = t.default_version()
            source = "assumed latest" if requested == "latest" else "configured default"
            if requested == "latest":
                warnings.append({"code": "VERSION_ASSUMED", "message": "No version evidence; using latest"})
    chosen, fallback = map_version(requested, available)
    if chosen is None:
        raise AlmanacError(
            "E_VERSION_UNAVAILABLE",
            f"No docs indexed for {t.name} {requested}",
            {"available": available},
        )
    if fallback:
        warnings.append({"code": "VERSION_FALLBACK", "message": f"Requested {requested}; using docs for {chosen}"})
    drop = {w for n in _names(t) for w in _TOKEN_RE.findall(n.lower())}
    return Interpretation(
        tech=t.id,
        tech_name=t.name,
        version=chosen,
        version_source=source,
        intent=["howto"],
        summary=f"{t.name} {chosen} ({source}): {query[:80]}",
        fts_query=build_fts_query(query, drop),
        warnings=warnings,
    )
