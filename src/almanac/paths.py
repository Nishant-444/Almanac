from __future__ import annotations

from pathlib import Path


class AlmanacPaths:
    """Every on-disk location Almanac uses, derived from the project root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def config(self) -> Path:
        return self.root / "almanac.yaml"

    @property
    def glossary(self) -> Path:
        return self.root / "glossary.yaml"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def manifest(self) -> Path:
        return self.docs / "manifest.json"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db(self) -> Path:
        return self.data / "almanac.db"

    @property
    def git_cache(self) -> Path:
        return self.data / "cache" / "git"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs / "almanac.jsonl"

    @property
    def query_log(self) -> Path:
        return self.logs / "queries.jsonl"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def golden(self) -> Path:
        return self.root / "golden"
