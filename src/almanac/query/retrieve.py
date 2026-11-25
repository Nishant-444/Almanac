from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from almanac.errors import AlmanacError
from almanac.query.understand import Interpretation, _tuple

_TRUST_BOOST = {1: 1.0, 2: 0.66, 3: 0.33, 4: 0.0}
_CANDIDATES = 40

_SQL = """
SELECT c.id AS chunk_id, p.page_key, p.title, c.heading_path, c.anchor, c.text, p.url,
       p.version, p.kind, p.trust, p.fetched_at, p.flags,
       bm25(chunk_fts, 1.0, 2.0, 0.5) AS rank
FROM chunk_fts
JOIN chunk c ON c.id = chunk_fts.rowid
JOIN page p ON p.id = c.page_id
WHERE chunk_fts MATCH ? AND p.tech_id = ? {version_clause}AND p.status = 'active'
ORDER BY rank
LIMIT ?
"""


@dataclass
class Hit:
    chunk_id: int
    page_id: str
    title: str
    heading_path: str
    anchor: str
    text: str
    url: str
    version: str
    kind: str
    trust: int
    fetched_at: str
    flags: list[str] = field(default_factory=list)
    score: float = 0.0


def _version_key(v: str) -> tuple:
    t = _tuple(v)
    return (v == "latest", t is None, t or (), v)


class Retriever:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def indexed_versions(self) -> dict[str, list[str]]:
        try:
            rows = self.conn.execute("SELECT DISTINCT tech_id, version FROM page WHERE status = 'active'").fetchall()
        except sqlite3.OperationalError as e:
            raise self._missing() from e
        if not rows:
            raise self._missing()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["tech_id"], []).append(r["version"])
        return {k: sorted(v, key=_version_key) for k, v in out.items()}

    @staticmethod
    def _missing() -> AlmanacError:
        return AlmanacError("E_INDEX_MISSING", "No index found; run: almanac fetch && almanac index")

    def search(self, i: Interpretation, k: int = 8, any_version: bool = False) -> list[Hit]:
        try:
            empty = self.conn.execute("SELECT 1 FROM page LIMIT 1").fetchone() is None
        except sqlite3.OperationalError as e:
            raise self._missing() from e
        if empty:
            raise self._missing()
        if i.fts_query is None:
            return []
        params: list = [i.fts_query, i.tech]
        clause = ""
        if not any_version:
            clause = "AND p.version = ? "
            params.append(i.version)
        params.append(_CANDIDATES)
        rows = self.conn.execute(_SQL.format(version_clause=clause), params).fetchall()
        hits = [
            Hit(
                chunk_id=r["chunk_id"],
                page_id=r["page_key"],
                title=r["title"],
                heading_path=r["heading_path"],
                anchor=r["anchor"],
                text=r["text"],
                url=r["url"],
                version=r["version"],
                kind=r["kind"],
                trust=r["trust"],
                fetched_at=r["fetched_at"],
                flags=json.loads(r["flags"] or "[]"),
                score=-r["rank"] * (1 + 0.15 * _TRUST_BOOST.get(r["trust"], 0.0)),
            )
            for r in rows
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
