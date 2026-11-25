from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


class FetchLog:
    def __init__(self, conn: sqlite3.Connection, run_id: str):
        self._conn = conn
        self.run_id = run_id
        self.source_id: str | None = None

    def record(self, url: str, status: int | None, bytes_: int, ms: int, error: str | None) -> None:
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            "INSERT INTO fetch_log(run_id, url, status, bytes, ms, error, at, source_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, url, status, bytes_, ms, error, at, self.source_id),
        )
        self._conn.commit()
