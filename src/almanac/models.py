from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, PositiveFloat, PositiveInt, model_validator
from pydantic_core import PydanticCustomError

_ID_RE = r"^[a-z0-9][a-z0-9_-]*$"
_VERSION_RE = r"^(latest|\d+(\.\d+)*(\.x)?)$"


class Kind(StrEnum):
    reference = "reference"
    guide = "guide"
    best_practice = "best_practice"
    changelog = "changelog"


class SourceType(StrEnum):
    llms_txt = "llms_txt"
    sitemap = "sitemap"
    git = "git"
    feed = "feed"
    crawl = "crawl"
    url_list = "url_list"


class Source(BaseModel, extra="forbid"):
    id: Annotated[str, Field(pattern=_ID_RE)]
    type: SourceType
    url: str | None = None
    urls: list[str] = []
    kind: Kind
    trust: int = Field(ge=1, le=4)
    include: list[str] = []
    exclude: list[str] = []
    version: Annotated[str, Field(pattern=_VERSION_RE)] | None = None
    paths: list[str] = []
    branch: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _check_locator(self) -> Source:
        if self.type is SourceType.url_list:
            if not self.urls:
                raise PydanticCustomError("missing_field", "urls is required for url_list sources", {"field": "urls"})
        elif not self.url:
            raise PydanticCustomError(
                "missing_field", "url is required for {type} sources", {"field": "url", "type": self.type.value}
            )
        return self


class Technology(BaseModel, extra="forbid"):
    id: Annotated[str, Field(pattern=_ID_RE)]
    name: str
    homepage: str | None = None
    aliases: list[str] = []
    versions: list[Annotated[str, Field(pattern=_VERSION_RE)]] = ["latest"]
    sources: list[Source]
    refresh_hours: PositiveInt | None = None

    def default_version(self) -> str:
        return self.versions[0]

    def stored_version(self) -> str:
        """The version pages are stored under when nothing more specific is known: first non-latest entry."""
        return next((v for v in self.versions if v != "latest"), "latest")


class Settings(BaseModel, extra="forbid"):
    data_dir: str = "./data"
    contact: str | None = None
    embedding_model: str = "bge-small-en"
    allow_private_network: bool = False
    response_token_budget: PositiveInt = 3000
    default_refresh_hours: PositiveInt = 24
    rate_limit_per_host_rps: PositiveFloat = 1
    stale_after_days: PositiveInt = 14
    max_concurrency: PositiveInt = 4
    crawl_max_depth: PositiveInt = 4
    max_pages_per_source: PositiveInt = 2000
    max_page_bytes: PositiveInt = 2_000_000
    max_redirects: PositiveInt = 5
    min_content_chars: PositiveInt = 200


class Config(BaseModel, extra="forbid"):
    settings: Settings = Settings()
    technologies: list[Technology] = []
    root: Path = Field(default=Path("."), exclude=True)

    def tech(self, id_or_alias: str) -> Technology | None:
        needle = id_or_alias.strip().casefold()
        for t in self.technologies:
            names = [t.id, t.name, *t.aliases]
            if any(n.casefold() == needle for n in names):
                return t
        return None


@dataclass
class RawDoc:
    source_id: str
    tech_id: str
    url: str
    final_url: str
    body: bytes
    content_type: str
    fetched_at: datetime  # UTC
    etag: str | None = None
    last_modified: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(kw_only=True)
class Page:
    id: str
    tech: str
    version: str
    kind: Kind
    trust: int
    title: str
    source_url: str
    source_id: str
    fetched_at: datetime
    content_hash: str
    last_modified: str | None = None
    flags: list[str] = field(default_factory=list)
    description: str | None = None
    canonical_url: str | None = None
    licence: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
    body: str


class ChangeKind(StrEnum):
    new = "new"
    changed = "changed"
    unchanged = "unchanged"
