# Almanac: Software Requirements Specification (SRS)

2026-11-24

## 1. Introduction

This SRS defines what Almanac shall do and how well, in enough detail to build and test it. It implements the goals in the Almanac PRD.

### 1.1 Purpose

To specify the functional, interface, data and quality requirements of Almanac v1.0, for the developer-builder and for any engineer or Claude Code session that implements or verifies it.

### 1.2 Scope

Almanac is a local application with five parts:

- a **source registry** describing which technologies and URLs to track
- a **fetch and clean pipeline** that turns web docs and repo docs into versioned markdown
- a **search index** combining keyword and semantic retrieval
- a **query layer** that understands plain-English questions and returns cited, structured answers
- an **MCP server and CLI** through which Claude Code and the user access all of it

Out of scope for v1.0: multi-user hosting, a web UI, authentication of remote users, paid cloud services, and indexing of private or paywalled content.

### 1.3 Definitions

| Term | Meaning |
| --- | --- |
| MCP | Model Context Protocol, the standard by which Claude Code calls external tools |
| Source | One registered documentation location (URL, sitemap, llms.txt or git repo) |
| Technology | A library, framework, language or tool tracked by Almanac, such as React |
| Page | One cleaned markdown document derived from a source |
| Chunk | A heading-bounded section of a page; the unit of retrieval |
| Bundle | The structured response returned for a query |
| FTS | Full-text search (SQLite FTS5) |
| Embedding | A numeric vector representing the meaning of a chunk or query |
| Hybrid search | Keyword plus semantic search with merged ranking |
| Golden set | A fixed list of questions with known correct answers, used for regression tests |
| Staleness | Age of a page since its last successful fetch |

### 1.4 Conventions

- **SHALL** marks a mandatory requirement, **SHOULD** a strong recommendation, **MAY** an option.
- Requirement IDs: `FR-<module>-<nn>` functional, `NFR-<area>-<nn>` non-functional, `IF-<nn>` interface, `DR-<nn>` data.
- Priority: **P0** needed for MVP, **P1** needed for v1.0, **P2** later.
- Every requirement is testable; Section 10 maps each to a verification method.

### 1.5 References

- Almanac PRD (companion document)
- Model Context Protocol specification and Claude Code documentation (current versions, to be confirmed at build time)
- SQLite FTS5 documentation
- robots.txt standard (RFC 9309)
- llms.txt convention for LLM-readable site documentation

## 2. Overall description

Almanac sits between public documentation on the web and Claude Code on the developer's machine, turning the first into a fast, local, queryable source for the second.

### 2.1 Product perspective

Almanac is a self-contained, new product. It has no dependency on a hosted Almanac service. It depends on public websites and repositories as inputs and on Claude Code as the main consumer. It is launched as a local process (stdio MCP server) by Claude Code, and as a CLI by the user or a scheduler.

### 2.2 Product functions (summary)

| Function | Summary |
| --- | --- |
| Source management | Define technologies, sources, versions and refresh intervals in config |
| Acquisition | Fetch from llms.txt, sitemaps, git repos, RSS/Atom and crawled pages |
| Normalisation | Convert to clean markdown with metadata; drop navigation and boilerplate |
| Indexing | Chunk by heading; build keyword and embedding indexes incrementally |
| Query understanding | Detect technology, version and intent; rewrite plain English into search queries |
| Retrieval | Hybrid search, re-ranking and version filtering |
| Response building | Assemble a cited bundle: answer, best practices, gotchas, related, example, what's new |
| Access | MCP tools for Claude Code; CLI for humans and schedulers |
| Freshness | Scheduled refresh, change detection, change digests |
| Quality | Golden-set tests, link and fetch health reports, query logs |

### 2.3 User classes

| User | Interaction | Needs |
| --- | --- | --- |
| Developer (primary) | CLI setup, config edits, occasional direct queries | Simple install, clear logs, control of sources |
| Claude Code (agent consumer) | MCP tool calls | Fast, structured, compact responses with citations |
| Maintainer (same person, later) | Tests, upgrades, adding technologies | Clean module boundaries, good tests |

### 2.4 Operating environment

- OS: Linux, macOS, and Windows through WSL. Native Windows is best effort.
- Runtime: Python 3.11 or newer (assumed; see PRD open questions).
- Disk: 2 GB free for a starter set of 10 technologies; embedding model files extra.
- Memory: 4 GB RAM minimum, 8 GB recommended while embedding.
- Network: needed for fetch and refresh only. Queries work fully offline.
- Client: Claude Code (terminal or VS Code) on a Claude Pro account.

### 2.5 Design and implementation constraints

- Single user, single machine in v1.
- No required paid services. Embeddings run locally.
- Storage uses open formats: markdown files, SQLite and YAML.
- MCP server uses the stdio transport so Claude Code can launch it directly.
- Fetching respects robots.txt, site terms and rate limits (see NFR-LEG).
- Pro-plan usage limits are shared with Claude chat; responses must stay compact to save tokens.

### 2.6 Assumptions and dependencies

- Target sites remain publicly reachable and keep a parseable structure.
- Claude Code continues to support MCP servers and project-level instructions.
- A small open-source embedding model gives acceptable retrieval quality on technical text.
- Docs licences allow local copies for personal use; redistribution is not intended.

## 3. System architecture

Almanac is a build-time pipeline that writes files and an index, plus a query server that only reads them, so a failed refresh can never damage answers.

*[Diagram: architecture · build-time pipeline and query-time path. See the live document for the drawing.]*

The top row builds the knowledge base on a schedule. The bottom row answers Claude Code's questions from the search index and never writes to it.

### 3.1 Components

| Component | Responsibility | Reads | Writes | Requirements |
| --- | --- | --- | --- | --- |
| Registry | Load and validate configuration | `almanac.yaml`, `glossary.yaml` | In-memory config | FR-REG |
| Fetcher | Acquire raw content politely | Web, git, feeds | Raw cache, `fetch_log` | FR-FET |
| Cleaner | Convert raw content to clean markdown with metadata | Raw cache | Pages | FR-CLN |
| Doc store | Durable pages and manifest | Pages | `docs/`, manifest | FR-STO |
| Indexer | Chunk, tag, embed, extract symbols | `docs/` | `almanac.db` | FR-IDX |
| Version detector | Find the project's technology versions | Project manifests and lockfiles (read only) | None | FR-VER |
| Query layer | Understand the question | Query, glossary, symbol table | None | FR-QUN |
| Retrieval | Hybrid search and ranking | `almanac.db` | None | FR-RET |
| Response builder (shown as "Bundle builder" in the diagram) | Assemble, validate and cite the bundle | Ranked chunks | None | FR-RSP |
| MCP server | Expose tools to Claude Code | All query-side components | `query_log` | FR-MCP |
| CLI | Human and scheduler entry point | Everything | Everything | FR-CLI |
| Scheduler | Trigger refresh runs | Config | Digests, reports | FR-SCH |

### 3.2 Runtime flows

**Refresh flow (build time)**

1. The scheduler or user starts `refresh --due`; a lock is taken.
2. The registry lists sources whose interval has elapsed.
3. The fetcher retrieves each source, skipping unchanged content.
4. The cleaner converts new or changed content to markdown pages.
5. The store writes pages, updates the manifest and marks vanished pages `removed`.
6. The indexer re-chunks and re-embeds changed pages only, then updates FTS, vectors and symbols in one transaction.
7. A change digest and health summary are written; the lock is released.
8. On any failure before step 6 commits, the previous index stays live.

**Query flow (query time)**

1. Claude Code calls an MCP tool with a plain-English request and the project path.
2. The version detector reads the project's manifests.
3. The query layer detects technology, version and intent, and rewrites the query.
4. Retrieval runs keyword and vector search per bundle section and fuses the results.
5. Confidence is checked; below threshold returns `not_found`.
6. The response builder assembles the bundle, attaches citations, runs the grounding validator and applies the token budget.
7. The query is logged locally; the bundle returns to Claude Code.

### 3.3 Module interfaces

Signatures are given in Python (the assumed build language) to fix boundaries; names may be refined during implementation.

```python
class Registry:        def load(path: Path) -> Config
class Fetcher:         def fetch(source: Source, manifest: Manifest) -> Iterator[RawDoc]
class Cleaner:         def clean(raw: RawDoc) -> Page
class Store:           def put(page: Page) -> ChangeKind        # new | changed | unchanged
class Indexer:         def index(changed: list[PageId]) -> IndexStats
class VersionDetector: def detect(project_path: Path, techs: list[str]) -> dict[str, VersionInfo]
class QueryLayer:      def understand(q: str, ctx: Context) -> Interpretation
class Retriever:       def search(i: Interpretation) -> RankedChunks
class Builder:         def build(i: Interpretation, r: RankedChunks, budget: int) -> Bundle
```

### 3.4 Technology choices (proposed, to confirm at build)

| Concern | Proposed | Alternatives | Reason |
| --- | --- | --- | --- |
| Language | Python 3.11 or newer | Node.js with TypeScript | Mature text, HTML and embedding libraries |
| HTTP client | `httpx` | `requests`, `aiohttp` | Async and sync, timeouts, HTTP/2 |
| Main-content extraction | `trafilatura` or `selectolax` with custom rules | `readability-lxml`, `BeautifulSoup` | Strips boilerplate; fast |
| HTML to markdown | `markdownify` or custom converter | `html2text`, `pandoc` | Keeps code fences and tables |
| Metadata and keyword index | SQLite with FTS5 | Tantivy, Whoosh | Single file, no service |
| Vector search | float32 blobs with in-memory matrix search | `sqlite-vec`, FAISS | Enough for 200,000 chunks; no extra service |
| Embeddings | Small local English model through `fastembed` or `sentence-transformers` | Larger model, hosted API | Local, free, private |
| MCP | Official MCP Python SDK, stdio transport | Custom JSON-RPC | Compatibility with Claude Code |
| CLI | Typer | argparse, Click | Typed, self-documenting |
| Config and validation | PyYAML with pydantic | TOML, JSON Schema | Clear errors |
| Tests | pytest plus golden runner | None | Standard |
| Packaging | `uv` or `pipx` | pip | Isolated install of CLI |

### 3.5 Key design decisions

- **Read and write separation.** Query-time code never writes to docs or fetches from the network. This protects accuracy and security.
- **Files are the source of truth; the index is disposable.** Any index can be rebuilt from `docs/`.
- **Hybrid retrieval.** Plain-English questions need semantic search; exact API names need keyword search. Both are required.
- **Extractive answers.** The builder assembles text from source chunks rather than generating new prose, which keeps the grounding rule enforceable.
- **Local embeddings.** No query data leaves the machine and no paid service is required.
- **stdio MCP server.** Claude Code launches and owns the process; there is nothing to host.

## 4. Functional requirements

Requirements are grouped by module. Query understanding and response details are expanded in Sections 7 and 8.

### 4.1 Source registry (FR-REG)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-REG-01 | The system SHALL read tracked technologies from a YAML file `almanac.yaml`. | P0 |
| FR-REG-02 | Each technology SHALL define id, display name, homepage, aliases, sources, version policy and refresh interval. | P0 |
| FR-REG-03 | Each source SHALL declare type (`llms_txt`, `sitemap`, `git`, `feed`, `crawl`, `url_list`), URL, kind (`reference`, `guide`, `best_practice`, `changelog`), trust tier (1 to 4) and include/exclude patterns. | P0 |
| FR-REG-04 | Config SHALL be validated on load; errors SHALL name the file, line and field. | P0 |
| FR-REG-05 | A technology SHALL support version pins (`18.x`, `3.12`) and the policy `latest`. | P0 |
| FR-REG-06 | `almanac add <tech>` SHOULD scaffold an entry by discovering llms.txt, sitemap and repo candidates and asking the user to confirm. | P1 |
| FR-REG-07 | Config changes SHALL take effect on the next run with no code change or rebuild. | P1 |

### 4.2 Fetcher (FR-FET)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-FET-01 | The fetcher SHALL try strategies in configured order; default: `llms-full.txt`, `llms.txt`, git, sitemap, crawl. | P0 |
| FR-FET-02 | It SHALL obey robots.txt and skip disallowed paths, logging each skip. | P0 |
| FR-FET-03 | It SHALL rate-limit per host (default 1 request/s) and cap concurrency (default 4). | P0 |
| FR-FET-04 | It SHALL send an identifying User-Agent with a configurable contact. | P0 |
| FR-FET-05 | It SHALL use conditional requests (ETag, Last-Modified) and content hashes to skip unchanged pages. | P0 |
| FR-FET-06 | It SHALL retry 429 and 5xx responses with exponential backoff, at most 3 times, honouring Retry-After. | P0 |
| FR-FET-07 | For `git` sources it SHALL shallow-clone or pull only configured paths and record the commit SHA. | P0 |
| FR-FET-08 | It SHALL cap crawl depth (default 4), pages per source (default 2,000) and page size (default 2 MB). | P0 |
| FR-FET-09 | It SHALL follow at most 5 redirects, canonicalise URLs and record the final URL. | P1 |
| FR-FET-10 | It SHALL ingest release notes from RSS, Atom and the GitHub releases API as `changelog` pages. | P1 |
| FR-FET-11 | It SHALL record per-URL outcome: status, duration, bytes, error. | P0 |
| FR-FET-12 | It MAY use a headless browser for pages that need JavaScript to render. | P2 |

### 4.3 Cleaner (FR-CLN)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-CLN-01 | Convert HTML to markdown preserving headings, lists, tables, links and fenced code with language tags. | P0 |
| FR-CLN-02 | Remove navigation, footers, cookie banners, ads and sidebars. | P0 |
| FR-CLN-03 | Convert relative links to absolute and keep heading anchors. | P0 |
| FR-CLN-04 | Extract title, description, canonical URL and last-modified date when present. | P0 |
| FR-CLN-05 | Detect doc version from URL path, page banner or source config and record it. | P0 |
| FR-CLN-06 | Normalise to UTF-8 and collapse redundant whitespace without altering code blocks. | P0 |
| FR-CLN-07 | Flag pages below a minimum content length (default 200 characters) as suspect. | P1 |
| FR-CLN-08 | Detect deprecation and removal markers ("deprecated", "removed in", "legacy") and set page flags. | P1 |

### 4.4 Store (FR-STO)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-STO-01 | Each page SHALL be written to `docs/<tech>/<version>/<slug>.md` with YAML front-matter (DR-01). | P0 |
| FR-STO-02 | A manifest SHALL record each page's source, hash and last fetch time. | P0 |
| FR-STO-03 | Pages that disappear from a source SHALL be marked `removed`, not silently deleted. | P1 |
| FR-STO-04 | The store SHOULD auto-commit to git after each refresh so changes can be diffed. | P1 |
| FR-STO-05 | The store directory SHALL be portable: copying it to another machine and re-indexing SHALL reproduce the same results. | P0 |

### 4.5 Indexer (FR-IDX)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-IDX-01 | Split pages into chunks along heading hierarchy, targeting 200 to 600 tokens, each with its heading path. | P0 |
| FR-IDX-02 | Keep fenced code blocks whole inside a chunk; split oversized code at logical boundaries. | P0 |
| FR-IDX-03 | Build an FTS5 index over chunk text, headings and tags. | P0 |
| FR-IDX-04 | Compute embeddings with a local model and store them with the model name and version. | P0 |
| FR-IDX-05 | Re-index only pages whose hash changed. | P0 |
| FR-IDX-06 | Attach metadata to each chunk: tech, version, kind, trust tier, URL, anchor, fetched time, flags, topics. | P0 |
| FR-IDX-07 | Extract API symbols (functions, classes, options, CLI flags) into a symbol table for exact-match lookup. | P1 |
| FR-IDX-08 | Rebuild all embeddings when the embedding model changes. | P0 |
| FR-IDX-09 | Tag chunks with topics derived from heading paths and keywords. | P1 |

### 4.6 Query understanding (FR-QUN)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-QUN-01 | Detect the technology from the query, aliases, active-file hints and project manifests. | P0 |
| FR-QUN-02 | Detect the version from explicit mention or project files; otherwise use `latest` and flag it. | P0 |
| FR-QUN-03 | Classify intent into the classes in Section 7.2. | P0 |
| FR-QUN-04 | Rewrite the query into 3 to 8 search queries using synonyms and official terms. | P0 |
| FR-QUN-05 | Parse pasted errors into error codes, symbols and library names. | P1 |
| FR-QUN-06 | Return a one-line interpretation of the question with every response. | P0 |
| FR-QUN-07 | On ambiguity, return labelled interpretations or ask one clarifying question. | P0 |
| FR-QUN-08 | Load a user-editable glossary mapping everyday terms to official terms. | P1 |

### 4.7 Retrieval (FR-RET)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-RET-01 | Run FTS and vector search in parallel and merge by reciprocal rank fusion. | P0 |
| FR-RET-02 | Filter by technology and version; if no match, fall back to the nearest version and warn. | P0 |
| FR-RET-03 | Boost by trust tier, exact symbol match, recency and matching intent kind. | P0 |
| FR-RET-04 | Re-rank the top N (default 30) down to K (default 8). | P1 |
| FR-RET-05 | Remove near-duplicate chunks. | P1 |
| FR-RET-06 | Return no results when confidence is below a threshold; never pad with weak matches. | P0 |
| FR-RET-07 | Retrieve separately per kind (reference, guide, best practice, changelog) to fill each bundle section. | P0 |

### 4.8 Response builder (FR-RSP)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-RSP-01 | Assemble the bundle defined in Section 8.1. | P0 |
| FR-RSP-02 | Attach to every statement a citation: source URL, version, fetched date. | P0 |
| FR-RSP-03 | Present code only as found in sources, and label any adaptation. | P0 |
| FR-RSP-04 | Enforce a token budget (default 3,000) by trimming lowest-priority sections first. | P0 |
| FR-RSP-05 | Show conflicts between sources with their trust tiers. | P1 |
| FR-RSP-06 | Add staleness warnings when pages exceed their refresh interval. | P0 |
| FR-RSP-07 | Add deprecation and removal warnings from page flags and changelogs. | P0 |
| FR-RSP-08 | Return a structured "not found" with nearby topics when nothing qualifies. | P0 |

### 4.9 MCP server (FR-MCP)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-MCP-01 | Serve over stdio so Claude Code can launch it directly. | P0 |
| FR-MCP-02 | Expose the tools in Section 6.1 with typed input schemas and descriptions that tell Claude when to call each one. | P0 |
| FR-MCP-03 | Return structured errors, never raw stack traces. | P0 |
| FR-MCP-04 | Be read-only at query time: no network fetch and no file modification. | P0 |
| FR-MCP-05 | Start in under 2 seconds and keep the index open between calls. | P1 |

### 4.10 CLI (FR-CLI)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-CLI-01 | Provide `init`, `add`, `fetch`, `index`, `refresh`, `search`, `serve`, `status`, `doctor`, `test`, `export` commands (Section 6.2). | P0 |
| FR-CLI-02 | Every command SHALL support `--json` output and meaningful exit codes. | P1 |
| FR-CLI-03 | `almanac doctor` SHALL check config, disk, model files, index integrity and MCP registration. | P1 |

### 4.11 Scheduler and freshness (FR-SCH)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-SCH-01 | `almanac refresh --due` SHALL refresh only sources whose interval has elapsed. | P0 |
| FR-SCH-02 | A lock SHALL prevent concurrent refresh runs. | P0 |
| FR-SCH-03 | Each run SHALL write a change digest (new, changed, removed pages; new releases) as markdown. | P1 |
| FR-SCH-04 | The project SHALL document setup for cron, systemd timers, launchd and GitHub Actions. | P1 |
| FR-SCH-05 | A failed source SHALL NOT block other sources; it SHALL be retried at the next run. | P0 |

### 4.12 Version detector (FR-VER)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-VER-01 | Read versions from `package.json`, lockfiles (npm, pnpm, yarn), `requirements.txt`, `pyproject.toml`, `poetry.lock`, `go.mod`, `Cargo.toml` and `Cargo.lock`. | P0 |
| FR-VER-02 | Resolve semver ranges to a concrete version, preferring lockfiles. | P0 |
| FR-VER-03 | Map a project version to the nearest indexed doc version and report the mapping. | P0 |
| FR-VER-04 | Support `pom.xml` and Gradle files. | P2 |

### 4.13 Observability and quality (FR-LOG)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-LOG-01 | Write structured logs (JSON lines) with levels and run IDs. | P0 |
| FR-LOG-02 | Log each query with interpretation, hits, outcome and latency; no data leaves the machine. | P0 |
| FR-LOG-03 | Produce a weekly report of weak or empty queries, broken links, empty pages and failed fetches. | P1 |
| FR-LOG-04 | Provide a golden-set runner that reports pass/fail per question and overall score. | P0 |

## 5. Data requirements

All durable data lives in a single project directory: markdown files, a YAML config and one SQLite database.

### 5.1 Directory layout (DR-01)

```text
almanac/
  almanac.yaml            # registry and settings
  glossary.yaml           # everyday term -> official term
  docs/
    react/
      19/
        hooks-use-memo.md
        ...
      latest -> 19      # symlink or manifest entry
    python/
      3.12/ ...
  data/
    almanac.db            # SQLite: metadata, FTS, vectors
    models/               # local embedding model files
  golden/
    questions.yaml        # golden test set
  reports/
    digest-2026-11-24.md  # change digests
    weekly-2026-w48.md    # quality report
  logs/
    almanac.jsonl
```

### 5.2 Page front-matter (DR-02)

Every stored page SHALL start with this YAML block.

```yaml
---
id: react/19/hooks-use-memo
tech: react
version: "19"
kind: reference        # reference | guide | best_practice | changelog
trust: 1               # 1 official docs, 2 official blog/RFC, 3 maintainer or vendor, 4 community
title: useMemo
source_url: https://example.org/reference/react/useMemo
source_id: react-docs
fetched_at: 2026-11-24T06:10:00Z
content_hash: sha256:...
last_modified: 2026-10-30
flags: []              # deprecated | removed | experimental | suspect
---
```

### 5.3 Database schema (DR-03)

```sql
CREATE TABLE tech   (id TEXT PRIMARY KEY, name TEXT, homepage TEXT, aliases TEXT);
CREATE TABLE source (id INTEGER PRIMARY KEY, tech_id TEXT REFERENCES tech(id),
  type TEXT, url TEXT, kind TEXT, trust INTEGER, interval_hours INTEGER,
  last_fetch TEXT, last_status TEXT);
CREATE TABLE page   (id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES source(id),
  tech_id TEXT, version TEXT, path TEXT UNIQUE, url TEXT, title TEXT, kind TEXT,
  trust INTEGER, content_hash TEXT, fetched_at TEXT, flags TEXT, status TEXT);
CREATE TABLE chunk  (id INTEGER PRIMARY KEY, page_id INTEGER REFERENCES page(id),
  ord INTEGER, heading_path TEXT, anchor TEXT, text TEXT, tokens INTEGER, topics TEXT);
CREATE VIRTUAL TABLE chunk_fts USING fts5(text, heading_path, topics,
  content='chunk', content_rowid='id');
CREATE TABLE chunk_vec (chunk_id INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB);
CREATE TABLE symbol (id INTEGER PRIMARY KEY, tech_id TEXT, version TEXT,
  name TEXT, kind TEXT, chunk_id INTEGER REFERENCES chunk(id));
CREATE TABLE fetch_log (id INTEGER PRIMARY KEY, run_id TEXT, url TEXT,
  status INTEGER, bytes INTEGER, ms INTEGER, error TEXT, at TEXT);
CREATE TABLE query_log (id INTEGER PRIMARY KEY, at TEXT, query TEXT,
  interpretation TEXT, tech_id TEXT, version TEXT, intent TEXT,
  hits INTEGER, outcome TEXT, ms INTEGER);
CREATE INDEX idx_page_tech_version ON page(tech_id, version);
CREATE INDEX idx_chunk_page ON chunk(page_id);
CREATE INDEX idx_symbol_name ON symbol(tech_id, name);
```

Vectors are stored as float32 blobs. For up to roughly 200,000 chunks, an in-memory matrix search is adequate. A vector extension for SQLite MAY replace it later without changing the schema's other tables.

### 5.4 Chunk model (DR-04)

| Field | Rule |
| --- | --- |
| Size | 200 to 600 tokens; hard max 900 |
| Boundary | Heading boundary first; paragraph boundary second; never inside a code block unless oversized |
| Context prefix | Heading path (for example `Hooks > useMemo > Caveats`) stored with the chunk and added to embeddings |
| Anchor | Slug of the nearest heading, for deep links |
| Overlap | None across heading boundaries; 1 sentence within long sections |
| Identity | Stable ID from page ID plus ordinal; changes only when the page hash changes |

### 5.5 Configuration schema (DR-05)

```yaml
settings:
  data_dir: ./data
  contact: you@example.com        # added to the User-Agent
  embedding_model: bge-small-en   # local model name
  response_token_budget: 3000
  default_refresh_hours: 24
  rate_limit_per_host_rps: 1
  stale_after_days: 14

technologies:
  - id: react
    name: React
    aliases: [reactjs, react.js]
    versions: ["19", latest]
    sources:
      - id: react-docs
        type: llms_txt            # llms_txt | sitemap | git | feed | crawl | url_list
        url: https://example.org/llms.txt
        kind: reference
        trust: 1
      - id: react-releases
        type: feed
        url: https://example.org/releases.atom
        kind: changelog
        trust: 1
      - id: react-practices
        type: url_list
        urls: [https://example.org/guides/performance]
        kind: best_practice
        trust: 2
    refresh_hours: 24
```

The example URLs are placeholders. Real URLs are filled in during setup.

### 5.6 Retention (DR-06)

- Pages: kept until the source drops them, then marked `removed` and kept 90 days.
- Fetch logs: 90 days. Query logs: 180 days, local only.
- Reports and digests: kept indefinitely; small.

## 6. External interface requirements

### 6.1 MCP tools (IF-01 to IF-09)

All tools SHALL accept and return JSON. Field names are fixed here; descriptions in the server SHALL tell Claude when to call each tool.

| ID | Tool | Inputs | Output |
| --- | --- | --- | --- |
| IF-01 | `search_docs` | `query` (string, required); `tech`, `version`, `project_path`, `intent` (optional); `max_tokens` (int, default 3000); `sections` (array of bundle sections) | Bundle (section 8.1) |
| IF-02 | `best_practices` | `tech` (required), `topic` (required), `version`, `max_tokens` | Practices list with rationale and sources |
| IF-03 | `explain_error` | `error_text` (required), `tech`, `project_path`, `max_tokens` | Probable causes, documented fixes, related changes |
| IF-04 | `whats_new` | `tech` (required), `since_version`, `until_version`, `since_date` | Changelog entries newest first; breaking and deprecated flags |
| IF-05 | `check_version` | `project_path` (required), `tech` | Detected versions, mapped doc versions, warnings |
| IF-06 | `get_page` | `id` or (`tech`, `path`); `version`, `section` | Cleaned markdown and metadata |
| IF-07 | `lookup_symbol` | `name` (required), `tech`, `version` | Matches with kind, signature, status, replacement, source |
| IF-08 | `list_technologies` | none | Technologies, versions, page counts, last refresh, health |
| IF-09 | `refresh_status` | `tech` | Per-source last fetch, status, next due, age |

Example input for `search_docs`:

```json
{
  "query": "my list keeps re-rendering when I type in the search box",
  "project_path": "/home/dev/shop-ui",
  "max_tokens": 2500
}
```

Input schema (abridged):

```json
{
  "type": "object",
  "required": ["query"],
  "properties": {
    "query":        {"type": "string", "minLength": 3, "maxLength": 2000},
    "tech":         {"type": "string"},
    "version":      {"type": "string"},
    "project_path": {"type": "string"},
    "intent":       {"enum": ["howto", "reference", "error", "concept", "compare", "migrate", "best_practice", "whats_new", "deprecation"]},
    "max_tokens":   {"type": "integer", "minimum": 500, "maximum": 12000, "default": 3000},
    "sections":     {"type": "array", "items": {"enum": ["answer", "best_practices", "gotchas", "related", "example", "whats_new"]}}
  }
}
```

### 6.2 CLI (IF-10)

| Command | Options | Exit codes |
| --- | --- | --- |
| `init` | `--dir`, `--model` | 0 ok, 2 bad args, 3 environment error |
| `add <tech>` | `--yes`, `--source` | 0, 2, 4 no candidates found |
| `fetch [tech]` | `--source`, `--force`, `--dry-run` | 0, 5 partial failures, 6 total failure |
| `index` | `--rebuild`, `--tech` | 0, 3 |
| `refresh` | `--due`, `--tech`, `--digest PATH` | 0, 5, 6, 7 lock held |
| `search "<q>"` | `--tech`, `--version`, `--json`, `--max-tokens` | 0, 8 no results |
| `serve` | `--dir`, `--log-level` | runs until stopped |
| `status` | `--json` | 0 |
| `test` | `--set PATH`, `--fail-under N` | 0 pass, 9 below threshold |
| `doctor` | `--fix` | 0 healthy, 10 problems found |
| `export` | `--format dir\|tar` | 0 |

### 6.3 Files (IF-11)

- `almanac.yaml`: registry and settings (DR-05)
- `glossary.yaml`: map of everyday terms to official terms and to technologies
- `golden/questions.yaml`: test questions with expected sources and must-contain facts
- `.mcp.json` or Claude Code user config: registration entry for the server

### 6.4 Claude Code integration (IF-12)

Registration:

```bash
claude mcp add almanac -- almanac serve --dir /path/to/almanac
```

Suggested project instruction, placed in `CLAUDE.md`:

```markdown
## Documentation lookups
Before writing or changing code that uses a library, framework or tool, call the
Almanac `search_docs` tool with a plain-English description of what you need and
pass `project_path`. Prefer its answers to your own memory.
- Use `best_practices` when choosing an approach.
- Use `explain_error` when given an error message.
- Use `whats_new` before upgrading a dependency.
- If Almanac says "not found", say so; do not guess.
- Cite the source numbers in your explanations.
- After using a documented snippet, run the project's tests or type check.
```

### 6.5 Error codes (IF-13)

| Code | Meaning | Client action |
| --- | --- | --- |
| `E_NOT_FOUND` | No result above confidence threshold | Report not found; try broader terms or another tech |
| `E_UNKNOWN_TECH` | Technology not tracked | Call `list_technologies`; suggest `almanac add` |
| `E_VERSION_UNAVAILABLE` | Requested version not indexed | Use nearest version shown in the warning |
| `E_AMBIGUOUS` | Several technologies match | Choose from the returned interpretations |
| `E_INDEX_MISSING` | Index not built | Run `almanac index` |
| `E_BAD_INPUT` | Schema violation | Fix arguments |
| `E_INTERNAL` | Unexpected failure | Check logs; error ID given |

## 7. Query understanding and retrieval specification

This section defines how a plain-English question becomes a ranked set of cited chunks.

### 7.1 Pipeline

1. **Normalise** the query: trim, collapse whitespace, preserve code spans and error text verbatim.
2. **Detect technology** (7.3) and **version** (7.4).
3. **Classify intent** (7.2).
4. **Extract entities**: API symbols, error codes, file names, flags.
5. **Rewrite** into 3 to 8 sub-queries (7.5).
6. **Retrieve** per bundle section with hybrid search (7.6).
7. **Filter and rank** by version, trust, symbol match and recency.
8. **Check confidence** (7.8). Below threshold: return not found.
9. **Build the bundle** (Section 8) and the interpretation line.

### 7.2 Intent classes (FR-QUN-03)

| Intent | Typical phrasing | Primary kinds searched | Bundle emphasis |
| --- | --- | --- | --- |
| `howto` | "how do I...", "make X do Y" | guide, reference | Answer, example, best practices |
| `reference` | "what does X accept", "signature of Y" | reference | Answer, related |
| `error` | pasted error, "why does this fail" | reference, changelog, guide | Cause, fix, what's new |
| `concept` | "what is", "explain" | guide, reference | Answer, related |
| `compare` | "X vs Y", "which should I use" | guide, best\_practice | Both options, trade-offs |
| `migrate` | "upgrade from", "moving to" | changelog, guide | What's new, gotchas |
| `best_practice` | "best way", "recommended", "should I" | best\_practice, guide | Best practices first |
| `whats_new` | "what changed", "latest" | changelog | What's new first |
| `deprecation` | "is X deprecated", "still supported" | changelog, reference | Status, replacement |

Classification SHALL use rules first (keywords, patterns, presence of error text). An optional small local model MAY break ties. A query MAY carry a primary and a secondary intent.

### 7.3 Technology detection (FR-QUN-01)

Sources of evidence, in descending priority:

1. Explicit `tech` argument
2. Name or alias in the query text, matched case-insensitively on word boundaries
3. API symbols in the query found in the symbol table
4. Imports or file types in `project_path` hints supplied by the client
5. Project manifests listing a tracked technology
6. Prior turn context passed by the client (optional)

If two technologies score within 15% of each other, the query is ambiguous (7.7).

### 7.4 Version detection (FR-QUN-02, FR-VER)

Precedence: explicit `version` argument; version named in the query; lockfile; manifest range resolved to the highest satisfying indexed version; configured default; `latest`.

The response SHALL state the source of the version ("from package-lock.json") and flag defaults as "assumed latest". If the exact version is not indexed, retrieval uses the nearest older indexed version within the same major line, then the nearest newer, and the response SHALL show the mapping.

### 7.5 Query rewriting (FR-QUN-04)

Rules applied in order:

- Replace everyday terms with official terms from the glossary.
- Add symbol candidates found by embedding the query against symbol names.
- Generate one question-form query, one keyword-form query and one symbol-form query.
- For `error` intent, add queries from the error code and the main message string without paths or line numbers.
- Cap at 8 sub-queries; drop near-duplicates.

Example:

| Original | Sub-queries |
| --- | --- |
| "stop my list re-rendering when typing" (React) | prevent unnecessary re-renders; memo component props; useMemo useCallback stable references; state colocation input; key prop list rendering |
| "popup closes when I click inside" (UI library) | modal dialog close on outside click; dialog onOpenChange; focus trap; event propagation in overlay |

### 7.6 Hybrid retrieval and ranking (FR-RET)

For each sub-query, run FTS5 (BM25) and vector search; take the top 40 of each. Merge with reciprocal rank fusion:

```latex
RRF(c) = \sum_{r \in R} \frac{1}{k + \mathrm{rank}_r(c)}, \quad k = 60
```

Then apply boosts. Initial weights below are defaults and SHALL be tuned against the golden set.

```latex
\mathrm{score}(c) = RRF(c) \cdot \left(1 + 0.20\,s + 0.15\,t + 0.10\,k + 0.10\,r\right)
```

| Symbol | Meaning | Value |
| --- | --- | --- |
| s | Exact symbol match in chunk or heading | 1 if match, else 0 |
| t | Trust tier boost | tier 1 = 1.0, tier 2 = 0.66, tier 3 = 0.33, tier 4 = 0 |
| k | Chunk kind matches intent's primary kinds | 1 or 0 |
| r | Recency of fetch relative to refresh interval | 1 if within interval, falls to 0 at three intervals |

Chunks with the `removed` page status SHALL be excluded. Chunks flagged `deprecated` SHALL remain searchable but carry the flag into the response.

After fusion, keep the top 30, optionally re-rank, deduplicate chunks with cosine similarity above 0.95, and keep the top 8 per bundle section.

### 7.7 Ambiguity handling (FR-QUN-07)

| Situation | Behaviour |
| --- | --- |
| Two technologies within 15% | Return `E_AMBIGUOUS` with labelled interpretations and a one-line example query for each |
| Technology unknown | `E_UNKNOWN_TECH` with closest tracked names |
| Technology clear, intent unclear | Use `howto` as primary and `reference` as secondary; state this in the interpretation |
| Query too short (under 3 words, no symbol) | Ask one clarifying question in the response |
| Term has multiple meanings in one technology (such as "state") | Return top chunks per meaning, grouped and labelled |

### 7.8 Confidence (FR-RET-06)

- Compute confidence from the top fused score relative to a calibration value learned from the golden set, plus agreement between FTS and vector top results.
- If confidence is below the threshold (default 0.35), return `E_NOT_FOUND` with up to 3 nearby topics.
- If confidence is between 0.35 and 0.55, return results with the warning "Low confidence match".

### 7.9 Error parsing (FR-QUN-05)

Extract, in order: error class or code (for example `ENOENT`, `TS2345`, `ModuleNotFoundError`), the first stable message sentence, symbol names in backticks or stack frames from tracked libraries. Strip file paths, line numbers, memory addresses and user-specific values before searching.

## 8. Response format and accuracy specification

### 8.1 Bundle schema (FR-RSP-01)

Every successful `search_docs` call SHALL return an object with these fields. Sections with no supporting source SHALL be omitted, not filled.

```json
{
  "status": "ok",
  "interpretation": {
    "tech": "react",
    "version": "19.0",
    "version_source": "package-lock.json",
    "intent": ["howto", "best_practice"],
    "summary": "React 19, preventing unnecessary re-renders",
    "confidence": 0.82
  },
  "warnings": [
    {"code": "STALE", "message": "2 sources older than 14 days", "refs": [3]}
  ],
  "answer":         {"text": "...", "refs": [1, 2]},
  "best_practices": [{"text": "...", "refs": [3]}],
  "gotchas":        [{"text": "...", "refs": [1], "flags": ["breaking"]}],
  "example":        {"code": "...", "language": "jsx", "adapted": false, "refs": [1]},
  "related":        [{"title": "useMemo", "id": "react/19/hooks-use-memo"}],
  "whats_new":      [{"text": "...", "version": "19.1", "refs": [4]}],
  "sources": [
    {"ref": 1, "title": "memo", "kind": "reference", "trust": 1,
     "version": "19", "url": "https://example.org/...#anchor", "fetched_at": "2026-11-23T06:10:00Z"}
  ],
  "meta": {"tokens": 1840, "ms": 212, "query_id": "q_01H..."}
}
```

The `not found` shape:

```json
{
  "status": "not_found",
  "interpretation": {"tech": "react", "version": "19.0", "summary": "..."},
  "message": "No indexed source answers this for React 19.",
  "nearby_topics": ["Rules of Hooks", "Effects"],
  "suggestions": ["Try the official term", "Check that the source is tracked: almanac status"]
}
```

### 8.2 Grounding rules (FR-RSP-02, FR-RSP-03)

- **G-1** Each sentence in `answer`, `best_practices`, `gotchas` and `whats_new` SHALL have at least one `refs` entry resolving to a `sources` item.
- **G-2** The response builder SHALL compose text only from retrieved chunk content (extractive, or minimal rewording that preserves meaning). It SHALL NOT add facts.
- **G-3** `example.code` SHALL be copied from a source chunk. If trimmed or adapted, `adapted` SHALL be `true` and the change SHALL be described.
- **G-4** A response validator SHALL reject any bundle that violates G-1 to G-3 and return `E_INTERNAL` in tests; at runtime it SHALL drop the offending statement.
- **G-5** When retrieved chunks disagree, both SHALL be shown with trust tiers; the higher tier SHALL be listed first.
- **G-6** `related` entries SHALL come from the page's links or from the same heading neighbourhood, not from generation.

### 8.3 Warning codes

| Code | Trigger |
| --- | --- |
| `STALE` | A cited page is older than `stale_after_days` |
| `VERSION_FALLBACK` | Doc version differs from project version |
| `VERSION_ASSUMED` | No version evidence; `latest` used |
| `DEPRECATED` | A cited API or page has a deprecated flag |
| `REMOVED` | A cited API is removed in the project's version |
| `LOW_CONFIDENCE` | Confidence between thresholds |
| `CONFLICT` | Sources disagree |
| `PARTIAL_COVERAGE` | Some sources failed or are unindexed |
| `TRUNCATED` | Token budget cut sections |

### 8.4 Token budget and trimming (FR-RSP-04)

When the bundle exceeds `max_tokens`, sections SHALL be trimmed in this order until it fits: `related`, `whats_new`, `example` (shortened), `best_practices` (fewest refs first), `gotchas`. `answer`, `warnings` and `sources` SHALL never be removed. A `TRUNCATED` warning SHALL list what was cut.

### 8.5 Ordering within a response

1. Interpretation
2. Warnings
3. Answer
4. Best practices
5. Gotchas
6. Example
7. Related
8. What's new
9. Sources

For `best_practice` intent, best practices move above the answer. For `whats_new` intent, what's new moves first.

## 9. Non-functional requirements

### 9.1 Performance (NFR-PERF)

| ID | Requirement | Pri |
| --- | --- | --- |
| NFR-PERF-01 | A warm `search_docs` call SHALL complete in under 1.5 s at p95 on an index of 100,000 chunks on a laptop with 8 GB RAM. | P0 |
| NFR-PERF-02 | The MCP server SHALL start in under 2 s and use under 1 GB RAM when idle. | P1 |
| NFR-PERF-03 | Incremental indexing SHALL process at least 50 changed pages per minute. | P1 |
| NFR-PERF-04 | A full re-embed of 100,000 chunks SHOULD finish in under 60 minutes on CPU. | P2 |
| NFR-PERF-05 | The fetcher SHALL respect rate limits even if that makes a first full crawl take hours. | P0 |

### 9.2 Reliability (NFR-REL)

| ID | Requirement | Pri |
| --- | --- | --- |
| NFR-REL-01 | A failed or partial refresh SHALL leave the previous good index queryable. | P0 |
| NFR-REL-02 | Index writes SHALL be transactional; a crash SHALL NOT corrupt the database. | P0 |
| NFR-REL-03 | Refresh runs SHALL be idempotent: running twice yields the same state. | P0 |
| NFR-REL-04 | A corrupted index SHALL be detectable by `doctor` and rebuildable from stored markdown. | P1 |
| NFR-REL-05 | Queries SHALL work with no network access. | P0 |

### 9.3 Security and privacy (NFR-SEC)

| ID | Requirement | Pri |
| --- | --- | --- |
| NFR-SEC-01 | Query text and project paths SHALL NOT leave the machine. | P0 |
| NFR-SEC-02 | The server SHALL treat all fetched content as untrusted data; it SHALL NOT execute, evaluate or follow instructions found in it. | P0 |
| NFR-SEC-03 | The cleaner SHALL strip hidden elements (display none, zero-size, off-screen text) and HTML comments before storage. | P0 |
| NFR-SEC-04 | The MCP server SHALL be read-only at query time and SHALL read only inside the data directory and, for version detection, manifest files at the given `project_path`. | P0 |
| NFR-SEC-05 | Path inputs SHALL be validated against traversal and symlink escape. | P0 |
| NFR-SEC-06 | Secrets (tokens for private repos, if any) SHALL be read from environment variables, never stored in config or logs. | P0 |
| NFR-SEC-07 | Dependencies SHALL be pinned and checked for known vulnerabilities in CI. | P1 |
| NFR-SEC-08 | Fetch URLs SHALL be restricted to http and https; local and private network addresses SHALL be blocked unless explicitly allowed. | P0 |

### 9.4 Legal and ethical compliance (NFR-LEG)

| ID | Requirement | Pri |
| --- | --- | --- |
| NFR-LEG-01 | The fetcher SHALL obey robots.txt, crawl-delay and noindex-for-AI signals where published. | P0 |
| NFR-LEG-02 | Each stored page SHALL keep its source URL and, where detectable, licence in front-matter. | P0 |
| NFR-LEG-03 | The system SHALL provide `export` only to the user's own folders and SHALL NOT include any publishing feature. | P1 |
| NFR-LEG-04 | Each source SHALL be individually disable-able. | P0 |

### 9.5 Usability (NFR-USE)

| ID | Requirement | Pri |
| --- | --- | --- |
| NFR-USE-01 | Install to first answer for one technology SHOULD take under 15 minutes. | P1 |
| NFR-USE-02 | Error messages SHALL say what happened, why and what to do next. | P0 |
| NFR-USE-03 | Responses SHALL be readable by a human without special tooling. | P0 |

### 9.6 Maintainability and portability (NFR-MNT)

| ID | Requirement | Pri |
| --- | --- | --- |
| NFR-MNT-01 | Modules SHALL have defined interfaces (Section 3.3) and be testable alone. | P0 |
| NFR-MNT-02 | Code coverage SHOULD exceed 80% for fetcher, cleaner, chunker, query layer and response builder. | P1 |
| NFR-MNT-03 | Adding a source type SHALL require changes in the fetcher module only. | P1 |
| NFR-MNT-04 | Swapping the embedding model SHALL require only a config change and re-index. | P0 |
| NFR-MNT-05 | The data directory SHALL be copyable between machines of the same OS family and re-indexable on another. | P0 |
| NFR-MNT-06 | Code SHALL be formatted, linted and type-checked in CI. | P1 |

### 9.7 Observability (NFR-OBS)

| ID | Requirement | Pri |
| --- | --- | --- |
| NFR-OBS-01 | Logs SHALL be JSON lines with run ID, module, level, message and duration. | P0 |
| NFR-OBS-02 | `status` SHALL show last refresh per source, error counts and index size. | P0 |
| NFR-OBS-03 | Every query SHALL record latency per stage (understanding, retrieval, building) for tuning. | P1 |

## 10. Testing and acceptance

Quality is proven by a golden set of real questions plus automated tests per module; a release is allowed only when all gates in 10.5 pass.

### 10.1 Test strategy

| Level | Scope | Tooling | Runs |
| --- | --- | --- | --- |
| Unit | Parsers, cleaner rules, chunker, version resolver, ranking math, response validator | pytest | Every commit |
| Integration | Fetch from a local fixture site and git repo; clean, store, index; query end to end | pytest with a local HTTP server | Every commit |
| Golden set | Real questions against a frozen snapshot of docs | `almanac test` | Before merging changes to crawler, chunker, ranking or model |
| Contract | MCP tool schemas and outputs against this SRS | JSON Schema validation | Every commit |
| Performance | Latency on 100,000-chunk synthetic index | Benchmark script | Before release |
| Security | Path traversal, hidden-text injection, SSRF, robots compliance | Targeted tests | Before release |
| Resilience | Kill process mid-refresh; corrupt index; unreachable sources | Fault-injection tests | Before release |
| Manual spot check | Five random answers compared with live official pages | Checklist | Weekly in use |

### 10.2 Golden set (FR-LOG-04)

Format:

```yaml
- id: react-rerender-001
  question: "my list keeps re-rendering when I type in the search box"
  project_fixture: fixtures/projects/react19-app
  expect:
    tech: react
    version: "19"
    intent: [howto, best_practice]
    top3_contains_page: react/19/memo
    must_include: ["props", "memo"]
    must_cite_kind: [reference]
- id: react-notfound-001
  question: "how do I configure the quantum flux option"
  expect:
    status: not_found
```

Composition rules:

- At least 30 questions at v1.0; at least 8 per technology.
- Spread: 35% how-to, 15% error, 15% best-practice, 10% concept, 10% what's-new or migration, 5% deprecation, 10% unanswerable (must return `not_found`).
- At least a third phrased casually, without API names.
- Each question carries the expected page, the facts that must appear and the version fixture.
- The set grows from real failures found during use (FR-LOG-03).

### 10.3 Acceptance criteria by module

| Module | Acceptance criteria |
| --- | --- |
| Registry | Valid config loads; 10 invalid configs each produce a precise error naming file, line and field |
| Fetcher | Fixture crawl obeys robots.txt and rate limit; unchanged pages are skipped on second run; 429 triggers backoff; git source records commit SHA |
| Cleaner | Fixture pages convert with headings, tables and fenced code intact; navigation text absent; hidden text removed |
| Store | Pages carry valid front-matter; vanished page becomes `removed`; copy to a new directory and re-index gives identical results |
| Indexer | Chunks within size limits; code blocks intact; second run indexes zero unchanged pages; embedding-model change triggers full rebuild |
| Query layer | Tech detected correctly on at least 95% of golden questions; ambiguous fixture returns `E_AMBIGUOUS`; pasted error yields code and symbols |
| Retrieval | At least 90% of golden questions have the expected page in the top 3; unanswerable questions return `not_found` |
| Response builder | 100% of statements cited; validator rejects an uncited statement in a unit test; token budget respected; warnings present for stale and fallback cases |
| Version detector | Correct version from each supported manifest and lockfile type, including range resolution |
| MCP server | Claude Code lists all tools; schemas validate; read-only enforced; error codes returned as specified |
| CLI | Every command has `--json`; exit codes match section 6.2 |
| Scheduler | `refresh --due` skips fresh sources; concurrent run exits with code 7; failed source does not block others |
| Observability | Query log records stages and outcomes; weekly report lists weak queries from a seeded log |

### 10.4 Traceability matrix (summary)

| PRD goal | Key requirements | Verified by |
| --- | --- | --- |
| G1 Fresh knowledge | FR-FET-05, FR-SCH-01 to 05, FR-RSP-06 | Scheduler and staleness tests; 2-week unattended run |
| G2 Accuracy | FR-RSP-02, FR-RSP-03, FR-RET-06, G-1 to G-6 | Validator unit tests; unanswerable golden questions |
| G3 Version correctness | FR-VER-01 to 03, FR-QUN-02, FR-RET-02 | Version fixtures in golden set |
| G4 Natural questions | FR-QUN-03 to 08, FR-RET-01 | Casual-phrasing share of golden set |
| G5 Richer answers | FR-RSP-01, FR-RET-07, FR-FET-10 | Bundle contract tests |
| G6 Seamless in Claude Code | FR-MCP-01 to 05, IF-12 | Manual session test; adoption review of query log |
| G7 Low upkeep | FR-REG-06, FR-CLI-03, NFR-USE-01 | Timed setup test; doctor test |
| G8 Local and private | NFR-SEC-01, NFR-REL-05, FR-MCP-04 | Offline test; network-deny test at query time |

### 10.5 Release gates

- All unit, integration and contract tests pass.
- Golden-set retrieval hit rate at or above 90% and citation coverage at 100%.
- p95 latency under 1.5 s on the benchmark index.
- Security and resilience tests pass.
- `almanac doctor` reports healthy on a clean install.
- Refresh has run unattended for 2 weeks with fetch health at or above 97%.

## 11. Deployment, operations and maintenance

### 11.1 Installation

1. Install the CLI in an isolated environment (`pipx install` or `uv tool install`).
2. Run `almanac init` in a chosen directory; it creates config, folders and downloads the embedding model once.
3. Add technologies with `almanac add`, then run `almanac fetch` and `almanac index`.
4. Register the server with Claude Code (section 6.4) and add the instruction to `CLAUDE.md`.
5. Run `almanac doctor` and `almanac test`.

### 11.2 Scheduling

| Environment | Recipe |
| --- | --- |
| Linux | systemd timer or cron entry running `almanac refresh --due --digest reports/` daily |
| macOS | launchd agent with the same command |
| Windows | Task Scheduler inside WSL, or run on demand |
| Always-on alternative | A scheduled CI job that refreshes and commits the store; the laptop pulls the result |

The refresh command SHALL be safe to run at any time and SHALL exit with the codes in section 6.2.

### 11.3 Backup and recovery

- The `docs/` directory and `almanac.yaml` are the durable assets; keep them in git.
- `data/almanac.db` can always be rebuilt with `almanac index --rebuild`.
- Recovery from a corrupted index: delete `almanac.db`, run `index --rebuild`; no fetch is needed.

### 11.4 Upgrades

- Schema changes SHALL carry a version number and an automatic migration, or a clear instruction to rebuild.
- Changing the embedding model triggers a full re-embed (FR-IDX-08); `doctor` SHALL warn when stored vectors use a different model than configured.
- Before upgrading dependencies, run the golden set and compare scores.

### 11.5 Routine operations

| Cadence | Task |
| --- | --- |
| Daily | Automatic refresh; check the change digest if a technology you use shipped a release |
| Weekly | Read the quality report; add glossary terms or sources for weak queries; spot-check five answers |
| Monthly | Review disabled or failing sources; prune old versions; update dependencies and run the golden set |
| On new project | `almanac add` any new technology and add 3 to 5 golden questions for it |

### 11.6 Troubleshooting guide

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Claude does not call Almanac | Server not registered or instruction missing | `claude mcp list`; check `CLAUDE.md`; run `doctor` |
| `E_INDEX_MISSING` | Index not built or deleted | `almanac index` |
| Answers cite old pages | Refresh not running or source failing | `almanac status`; check logs and the digest |
| Wrong version used | No manifest found at `project_path` | Pass `project_path` or `version` explicitly |
| Poor results for casual wording | Missing glossary terms | Add entries; rerun golden set |
| Many `PARTIAL_COVERAGE` warnings | Source layout changed or blocked | Check fetch log; adjust selectors or switch to a git source |

## 12. Appendices

### Appendix A: Example glossary file

```yaml
terms:
  popup:        [modal, dialog, overlay]
  login:        [authentication, sign in, session]
  re-render:    [render, memo, reconciliation]
  slow list:    [virtualization, windowing, pagination]
  state mgmt:   [state management, store, context]
scopes:
  react:   [hook, component, props, jsx]
  django:  [view, model, queryset, middleware]
```

### Appendix B: Example change digest

```markdown
# Almanac digest 2026-11-24

## react (19)
- New release 19.1 (feed): 3 notes mention deprecations
- Changed pages (4): hooks/useMemo, reference/memo, ...
- Newly flagged deprecated: `exampleOption` -> use `exampleReplacement`

## Health
- Sources fetched: 11 of 11
- Pages: 2,431 (+7 new, 12 changed, 1 removed)
- Suspect pages: 2 (under 200 characters)
```

Names in the example are illustrative.

### Appendix C: Example `explain_error` response

```json
{
  "status": "ok",
  "interpretation": {"tech": "python", "version": "3.12", "intent": ["error"],
    "summary": "ModuleNotFoundError when importing a package"},
  "answer": {"text": "Python could not find the module on its import path ...", "refs": [1]},
  "best_practices": [{"text": "Use a virtual environment per project ...", "refs": [2]}],
  "related": [{"title": "The import system", "id": "python/3.12/reference-import"}],
  "sources": [{"ref": 1, "title": "Import system", "kind": "reference", "trust": 1,
    "version": "3.12", "url": "https://example.org/...", "fetched_at": "2026-11-23T06:10:00Z"}]
}
```

The content shown is placeholder text, not real documentation.

### Appendix D: Requirement ID index

| Prefix | Area | Section |
| --- | --- | --- |
| FR-REG | Source registry | 4.1 |
| FR-FET | Fetcher | 4.2 |
| FR-CLN | Cleaner | 4.3 |
| FR-STO | Store | 4.4 |
| FR-IDX | Indexer | 4.5 |
| FR-QUN | Query understanding | 4.6, 7 |
| FR-RET | Retrieval | 4.7, 7.6 |
| FR-RSP | Response builder | 4.8, 8 |
| FR-MCP | MCP server | 4.9 |
| FR-CLI | CLI | 4.10 |
| FR-SCH | Scheduler | 4.11 |
| FR-VER | Version detector | 4.12 |
| FR-LOG | Observability and quality | 4.13 |
| IF | Interfaces | 6 |
| DR | Data | 5 |
| NFR | Non-functional | 9 |

### Appendix E: Open items carried from the PRD

- First technologies and trusted best-practice sources (PRD Q1, Q3)
- Final build language (PRD Q2); this SRS assumes Python
- Choice of embedding model after golden-set comparison (PRD Q6)
- Real source URLs, to be added to `almanac.yaml` during setup
