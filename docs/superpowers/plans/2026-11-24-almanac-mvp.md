# Almanac MVP (v0.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the core loop end to end. Fetch docs for 2–3 technologies, store them as versioned markdown, index them with SQLite FTS5, and answer a plain-English question inside Claude Code with a cited answer through a stdio MCP server.

**Architecture:** There is a build-time pipeline (registry → fetcher → cleaner → store → chunker/indexer) that writes markdown files and `data/almanac.db`. There is also a read-only query path (understand → FTS retrieve → bundle builder) behind a thin MCP server and a Typer CLI. Files are the source of truth and the index can always be rebuilt from `docs/`.

**Tech Stack:** Python ≥3.11 (dev pinned to 3.12 via uv), httpx, beautifulsoup4 (`html.parser`), markdownify, defusedxml, ruamel.yaml, pydantic v2, Typer, official `mcp` Python SDK (FastMCP, stdio), SQLite FTS5, pytest, ruff.

**Spec:** `docs/Almanac-SRS.md` (requirements) and `docs/Almanac-PRD.md` (release scope: §6.1, §11.1–11.2). This plan implements the PRD's MVP row: F-01 to F-07, milestones M1 to M3.

## Scope decisions (made here so tasks don't re-decide them)

- **In MVP:** source types `llms_txt` (including `llms-full.txt`), `sitemap`, `url_list` and `git`. Keyword (FTS5) retrieval only. MCP tools are `search_docs`, `get_page` and `list_technologies`. CLI commands are `init`, `add`, `fetch`, `index`, `search`, `serve` and `status`.
- **Deferred to v1.0 plans (M4–M10):** embeddings and hybrid search; intent classification (MVP always reports `["howto"]`); glossary and query rewriting; project version detection (`project_path` is accepted and path-validated but unused); `feed` and `crawl` fetching (config accepts them, and fetch skips them with a logged "not supported until v1.0", counted as *skipped*, not failed); source discovery in `add`; `refresh`, `test`, `doctor` and `export`; the full bundle sections (best practices, gotchas, example, what's new); marking pages `removed`; git auto-commit of the store.
- **Deliberate deviations from SRS DR-03:** `source.id` and `page.source_id` are `TEXT` holding the config source id. The query log goes to `logs/queries.jsonl` rather than the `query_log` table, so the query path never writes to the DB that a refresh may be writing. A `meta(key TEXT PRIMARY KEY, value TEXT)` table holds `schema_version = "1"`.
- **MVP bundle:** follows SRS §8.1 for `status`, `interpretation`, `warnings`, `answer`, `sources` and `meta`. It adds an `excerpts` list (`[{heading_path, text, refs}]`) for hits 2–5, which v1.0 replaces with the per-section fields.
- Fetching is sequential, which satisfies the concurrency cap of 4.

## Global Constraints

- `requires-python = ">=3.11"`. Dev interpreter pinned with `uv python pin 3.12` because native wheels may lag on 3.15.
- Dependencies are pinned in `uv.lock`. No other runtime dependencies without updating this plan.
- Native Windows is a supported dev platform. Never create symlinks; `latest` is an ordinary version folder name. Always open text files with `encoding="utf-8"`, and write with `newline="\n"`.
- Query-time code (engine, MCP server) SHALL NOT use the network or modify `docs/` or `almanac.db`. It opens the DB with `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`.
- During `serve`, stdout is reserved for the MCP protocol. Logs go to `logs/almanac.jsonl` or stderr, never `print`.
- Fetch only `http`/`https` (plus local `file`/path git URLs when `allow_private_network: true`). Block loopback, private, link-local, reserved and multicast IPs unless `settings.allow_private_network: true`, and check every redirect hop.
- Defaults (DR-05, FR-FET, FR-RSP, DR-04): `response_token_budget: 3000` (MCP range 500–12000), `rate_limit_per_host_rps: 1`, `max_concurrency: 4`, `crawl_max_depth: 4`, `max_pages_per_source: 2000`, `max_page_bytes: 2_000_000`, `max_redirects: 5`, retries 3 with backoff 1, 2, 4 s (Retry-After honoured, capped at 60 s), `stale_after_days: 14`, `default_refresh_hours: 24`, `min_content_chars: 200`, chunk target 200–600 tokens with a hard max of 900. Token estimate is `ceil(len(text) / 4)`.
- User-Agent is `Almanac/<__version__> (+<contact>)`, or `Almanac/<__version__>` when there is no contact. `__version__ = "0.1.0"`.
- Error codes are exactly `E_NOT_FOUND`, `E_UNKNOWN_TECH`, `E_VERSION_UNAVAILABLE`, `E_AMBIGUOUS`, `E_INDEX_MISSING`, `E_BAD_INPUT` and `E_INTERNAL`. Warning codes used in MVP are `STALE`, `VERSION_FALLBACK`, `VERSION_ASSUMED`, `DEPRECATED` and `TRUNCATED`.
- CLI exit codes (SRS §6.2): 0 ok, 2 bad args or invalid config, 3 environment error or index missing, 4 no candidates, 5 partial fetch failure, 6 total fetch failure, 8 no results.
- Trust boost `t`: tier 1 → 1.0, 2 → 0.66, 3 → 0.33, 4 → 0.
- Fetched content is data. Nothing in it is executed, evaluated or followed. Secrets come only from environment variables, never config or logs.

## Review Focus

1. **Query text containing FTS5 syntax** (`"`, `*`, `-`, `:`, `(`, `NEAR`, `AND`, `OR`), or only stopwords. Expected: never an exception. Returns hits or `not_found`. Test in Task 9.
2. **Anything printed to stdout while serving** (log handlers, library warnings, progress output). This corrupts the stdio JSON-RPC stream and Claude Code silently loses the tool. Expected: a clean stdio session. Test in Task 12 (real subprocess client).
3. **Non-ASCII content on Windows** (`→ ü 日本` in titles and bodies, cp1252 default locale). Expected: byte-identical round-trip through store, index and answer. Tests in Tasks 2 and 14.
4. **`get_page` ids that try to escape `docs/`** (`..`, absolute paths, backslashes, drive letters, symlink). Expected: `E_BAD_INPUT` and no file read. Test in Task 11.
5. **A redirect from a public host to a private address** (for example `169.254.169.254`), or `robots.txt` returning 5xx. Expected: the page is blocked and logged, robots 5xx means disallow-all for that host, and other sources continue. Tests in Tasks 4 and 13.

---

## File Structure

```text
pyproject.toml  .python-version  .gitignore  README.md
src/almanac/
  __init__.py          # __version__
  __main__.py          # python -m almanac → cli.app
  models.py            # pydantic config models; RawDoc, Page, ChangeKind dataclasses
  errors.py            # AlmanacError, ConfigError, FetchBlocked, FetchFailed
  paths.py             # AlmanacPaths: every on-disk location
  registry.py          # load_config (validation with file:line:field)
  store.py             # slugify, page (de)serialisation, Manifest, Store
  cleaner.py           # RawDoc -> Page
  db.py                # schema, connect()
  chunker.py           # Page -> list[Chunk]; estimate_tokens
  indexer.py           # Indexer
  fetch/
    safety.py          # check_url
    ratelimit.py       # RateLimiter
    robots.py          # RobotsCache
    fetchlog.py        # FetchLog (fetch_log table)
    http.py            # HttpClient
    web.py             # WebFetcher: llms_txt, sitemap, url_list
    git.py             # GitFetcher
  query/
    understand.py      # Interpretation, understand, map_version, build_fts_query
    retrieve.py        # Hit, Retriever
  response/
    bundle.py          # build_bundle, not_found, validate_bundle, render_markdown
  engine.py            # Engine: search_docs, get_page, list_technologies (read-only)
  mcp_server.py        # create_server, main
  logs.py              # JSON-lines logger
  pipeline.py          # run_fetch, run_index
  cli.py               # Typer app
tests/
  conftest.py          # tmp almanac root, docs_site HTTP fixture, indexed_root
  fixtures/config/*.yaml  fixtures/html/*.html  fixtures/site/**
  test_<module>.py per module, test_e2e.py
```

---

### Task 1: Scaffold, config models, registry

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `src/almanac/__init__.py`, `src/almanac/__main__.py` (stub), `src/almanac/models.py`, `src/almanac/errors.py`, `src/almanac/paths.py`, `src/almanac/registry.py`
- Test: `tests/test_registry.py`, `tests/fixtures/config/valid.yaml`

**Interfaces:**
- Produces:
  - `models.py`:
    - `Kind(StrEnum)`: `reference`, `guide`, `best_practice`, `changelog`.
    - `SourceType(StrEnum)`: `llms_txt`, `sitemap`, `git`, `feed`, `crawl`, `url_list`.
    - `Source(BaseModel, extra="forbid")`: `id: str`, `type: SourceType`, `url: str | None = None`, `urls: list[str] = []`, `kind: Kind`, `trust: int` (1–4), `include: list[str] = []`, `exclude: list[str] = []` (fnmatch globs on the URL), `version: str | None = None`, `paths: list[str] = []` (git), `branch: str | None = None`, `enabled: bool = True`.
    - `Technology(extra="forbid")`: `id` (regex `^[a-z0-9][a-z0-9_-]*$`), `name: str`, `homepage: str | None = None`, `aliases: list[str] = []`, `versions: list[str] = ["latest"]` (each matches `^(latest|\d+(\.\d+)*(\.x)?)$`), `sources: list[Source]`, `refresh_hours: int | None = None`, and method `default_version() -> str` (first entry of `versions`).
    - `Settings(extra="forbid")`: every default listed in Global Constraints, plus `data_dir: str = "./data"`, `contact: str | None = None`, `embedding_model: str = "bge-small-en"` and `allow_private_network: bool = False`. Numeric fields must be > 0.
    - `Config`: `settings: Settings = Settings()`, `technologies: list[Technology] = []`, `root: Path` (excluded from validation and set by the loader), and method `tech(id_or_alias: str) -> Technology | None` (case-insensitive over id, name and aliases).
    - `RawDoc` dataclass: `source_id`, `tech_id`, `url`, `final_url`, `body: bytes`, `content_type: str`, `fetched_at: datetime` (UTC), `etag: str | None`, `last_modified: str | None`, `extra: dict[str, str]`.
    - `Page` dataclass with the DR-02 fields: `id`, `tech`, `version`, `kind`, `trust`, `title`, `source_url`, `source_id`, `fetched_at`, `content_hash`, `last_modified`, `flags: list[str]`, `description: str | None`, `canonical_url: str | None`, `licence: str | None`, `extra: dict[str, str]` and `body: str`.
    - `ChangeKind(StrEnum)`: `new`, `changed`, `unchanged`.
  - `errors.py`:
    - `AlmanacError(code: str, message: str, details: dict | None = None)`.
    - `ConfigError(file: Path, line: int | None, field: str, message: str)`, where `str()` gives `"{file}:{line}: {field}: {message}"`.
    - `FetchBlocked(reason: str)` and `FetchFailed(reason: str)`.
  - `paths.py`: `AlmanacPaths(root: Path)` with properties `config`, `glossary`, `docs`, `manifest` (`docs/manifest.json`), `data`, `db` (`data/almanac.db`), `git_cache` (`data/cache/git`), `logs`, `log_file`, `query_log` (`logs/queries.jsonl`), `reports` and `golden`.
  - `registry.py`: `load_config(path: Path) -> Config`, which raises `ConfigError`.

- [ ] **Step 1: Initialise the repository and project**

```bash
git init && uv init --lib --name almanac --python 3.12 && uv python pin 3.12
uv add httpx beautifulsoup4 markdownify defusedxml "ruamel.yaml" "pydantic>=2" typer mcp
uv add --dev pytest ruff
```
Set `[project.scripts] almanac = "almanac.cli:app"`. Add `.venv/`, `__pycache__/`, `.pytest_cache/` and `/tmp-almanac*/` to `.gitignore`.

- [ ] **Step 2: Write the failing tests** in `tests/test_registry.py`

`valid.yaml` is the SRS DR-05 example plus a second tech `python` (versions `["3.12"]`, one `git` source with `paths: [Doc]`).

```python
def test_load_valid_config(fixtures):
    cfg = load_config(fixtures / "config/valid.yaml")
    react = cfg.tech("ReactJS")
    assert react.id == "react" and react.default_version() == "19"
    assert [s.type for s in react.sources] == ["llms_txt", "feed", "url_list"]
    s = cfg.settings
    assert (s.response_token_budget, s.rate_limit_per_host_rps, s.stale_after_days) == (3000, 1, 14)
    assert (s.max_pages_per_source, s.max_page_bytes, s.crawl_max_depth, s.max_concurrency) == (2000, 2_000_000, 4, 4)
    assert s.allow_private_network is False
    assert cfg.root == (fixtures / "config").resolve()

@pytest.mark.parametrize("yaml_text, field, line", INVALID_CASES)
def test_invalid_config_names_file_line_field(tmp_path, yaml_text, field, line):
    p = tmp_path / "almanac.yaml"; p.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert e.value.file == p and e.value.field == field and e.value.line == line
    assert str(e.value).startswith(f"{p}:{line}: {field}: ")
```

`INVALID_CASES` holds 12 small documents, each with the expected `(field, line)`:
- missing `technologies[0].id`
- `trust: 5`
- `type: ftp`
- `kind: tutorial`
- `url_list` with no `urls`
- `llms_txt` with no `url`
- version `"nineteen"`
- a duplicate source id across two techs (field `technologies[1].sources[0].id`)
- an unknown top-level key `setings` (field `setings`)
- tech id `My React`
- `rate_limit_per_host_rps: -1`
- a YAML syntax error (field `<yaml>`, line from the parser)

Lines are 1-based.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 4: Implement the models, errors, paths and `load_config`**

Parse with `ruamel.yaml.YAML(typ="rt")` so nodes carry positions. Map each pydantic error `loc` to a line by walking the round-trip tree. For a mapping key, use `node.lc.key(k)[0] + 1`. For a list item, use `node.lc.item(i)[0] + 1`. When the leaf is missing (a required field), use the line of the deepest existing parent. Render the field as dotted with `[i]` indices. Check for a duplicate source id after pydantic validation passes.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -v`
Expected: 13 passed.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: project scaffold, config models and validated registry"
```

---

### Task 2: Store and manifest

**Files:**
- Create: `src/almanac/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Page` and `ChangeKind` (Task 1).
- Produces:
  - `slugify_url(url: str) -> str`:
    - Takes the URL path and drops the `.html`, `.htm`, `.md`, `.mdx` or `.txt` suffix.
    - Turns `/` and non-`[a-z0-9]` characters into `-` after lowercasing, collapses runs of `-` and trims them.
    - Returns `index` if the result is empty. Truncates to 120 characters.
  - `content_hash(body: str) -> str`: returns `"sha256:" + hexdigest` of the UTF-8 body.
  - `page_to_text(page: Page) -> str` and `page_from_text(text: str) -> Page`: `---` YAML front-matter in DR-02 field order, then the body.
  - `Manifest`:
    - `Manifest.load(path: Path) -> Manifest`: a missing file gives an empty manifest.
    - `http_validators(url) -> tuple[str | None, str | None]` and `set_http_validators(url, etag, last_modified)`.
    - `source_state(source_id) -> dict` and `set_source_state(source_id, **values)`.
    - `page_entry(page_id) -> dict | None`, `record_page(page: Page, path: Path)` and `page_id_for_url(tech, version, url) -> str | None`.
    - `save()`: atomic, via a temporary file and `os.replace`.
  - `Store(docs_dir: Path, manifest: Manifest)`:
    - `make_page_id(tech, version, url) -> str`: `f"{tech}/{version}/{slug}"`. If that slug is already taken by a different URL, append `-` plus `sha1(url)[:8]`. Reuses the existing id for a known URL.
    - `path_for(page_id) -> Path`
    - `put(page) -> ChangeKind`
    - `read(page_id) -> Page`
    - `iter_pages(tech: str | None = None) -> Iterator[Page]`

- [ ] **Step 1: Write the failing tests**

```python
def test_slugify_url():
    assert slugify_url("https://react.dev/reference/react/useMemo") == "reference-react-usememo"
    assert slugify_url("https://x.dev/docs/Intro.HTML") == "docs-intro"
    assert slugify_url("https://x.dev/") == "index"

def test_put_new_then_unchanged_then_changed(tmp_path, make_page):
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "docs/manifest.json"))
    p = make_page(id="react/19/memo", body="# memo\nText")
    assert store.put(p) is ChangeKind.new
    assert (tmp_path / "docs/react/19/memo.md").exists()
    assert store.put(p) is ChangeKind.unchanged
    assert store.put(replace(p, body="# memo\nNew")) is ChangeKind.changed
    assert store.manifest.page_entry("react/19/memo")["content_hash"] == content_hash("# memo\nNew")

def test_unicode_round_trip_is_byte_identical(tmp_path, make_page):  # Review Focus 3
    body = "# Pfeile → ü 日本\n\n```py\nprint('日本')\n```\n"
    store = Store(tmp_path / "docs", Manifest.load(tmp_path / "m.json"))
    store.put(make_page(id="t/1/u", title="Ü → 日本", body=body))
    raw = (tmp_path / "docs/t/1/u.md").read_bytes()
    assert b"\r\n" not in raw and "日本".encode() in raw
    assert store.read("t/1/u").body == body and store.read("t/1/u").title == "Ü → 日本"

def test_slug_collision_gets_hash_suffix(tmp_path): ...  # /a/b and /a-b, same tech/version → ids differ, second ends with -<8 hex>
def test_manifest_save_is_atomic_and_reloads(tmp_path): ...  # validators + source_state survive reload
def test_page_text_round_trip(make_page): ...  # page_from_text(page_to_text(p)) == p, fetched_at stays tz-aware UTC
```
Add a `make_page(**overrides)` factory fixture to `tests/conftest.py`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `store.py`**

`put` compares `content_hash` against the manifest. When unchanged, it does not rewrite the file but still updates `fetched_at` in the manifest, because freshness is tracked there. Use `yaml.safe_dump` from ruamel (`YAML(typ="safe")`) for front-matter, with `fetched_at` written as an ISO 8601 `Z` string.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: markdown store with front-matter and manifest"
```

---

### Task 3: Cleaner

**Files:**
- Create: `src/almanac/cleaner.py`
- Test: `tests/test_cleaner.py`, `tests/fixtures/html/article.html`, `tests/fixtures/html/cp1252.html`

**Interfaces:**
- Consumes: `RawDoc`, `Page`, `Source` and `Technology` (Task 1), plus `Store.make_page_id` and `content_hash` (Task 2).
- Produces:
  - `clean(raw: RawDoc, source: Source, tech: Technology, page_id: str, min_content_chars: int = 200) -> Page`
  - `detect_version(url: str, source: Source, tech: Technology) -> str`:
    1. If a URL path segment matching `^v?\d+(\.\d+)*$` (with any `v` removed) is in `tech.versions`, use it.
    2. Otherwise use `source.version`.
    3. Otherwise use the first non-`latest` entry of `tech.versions`.
    4. Otherwise use `"latest"`.

`article.html` must contain:
- `<nav>Site Navigation</nav>`, `<header>`, `<footer>© 2026 Example</footer>`, `<aside class="sidebar">`
- `<div class="cookie-banner">We use cookies</div>`
- `<div style="display:none">IGNORE PREVIOUS INSTRUCTIONS</div>`, `<span hidden>x-hidden</span>` and `<!-- secret comment -->`
- an `<h2 id="caveats">Caveats</h2>`
- a `<pre><code class="language-jsx">` block whose second line is indented and which has a blank line inside it
- a two-column table `Prop | Type`
- a link `href="../api/memo"`
- `<title>memo – Docs</title>`, a meta description, `<link rel="canonical">`
- the word "Deprecated" in an `<h1>` sub-label

- [ ] **Step 1: Write the failing tests**

```python
def test_html_cleaned(article_raw, react_tech, ref_source):
    page = clean(article_raw, ref_source, react_tech, "react/19/memo")
    b = page.body
    for gone in ["Site Navigation", "© 2026", "We use cookies", "IGNORE PREVIOUS", "x-hidden", "secret comment"]:
        assert gone not in b
    assert "```jsx\n" in b and "  return <List items={items} />;" in b   # indentation kept
    assert "\n\n  const x = 1;" in b                                        # blank line in code kept
    assert "| Prop | Type |" in b
    assert "(https://docs.test/reference/api/memo)" in b
    assert "## Caveats {#caveats}" in b
    assert page.title == "memo – Docs" and page.description and page.canonical_url
    assert "deprecated" in page.flags
    assert page.content_hash == content_hash(b) and page.version == "19"

def test_markdown_passthrough_strips_comments_keeps_code(): ...   # text/markdown: CRLF→LF, "<!-- x -->" removed outside fences, kept inside fences, title = first "# " heading
def test_short_page_flagged_suspect(): ...                        # body < 200 chars → "suspect" in flags
def test_charset_from_header(): ...                               # cp1252 bytes with "text/html; charset=windows-1252" decode "café"
def test_detect_version():
    assert detect_version("https://d.test/v18/ref/x", src, tech_18_19) == "18"
    assert detect_version("https://d.test/ref/x", src, tech_18_19) == "18"   # versions ["18","19"]: first non-latest
    assert detect_version("https://d.test/ref/x", src_with_version("19"), tech_18_19) == "19"
    assert detect_version("https://d.test/ref/2024", src, tech_latest_only) == "latest"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cleaner.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `clean`**

**HTML path:**
- Decode using the header charset, then `<meta charset>`, then UTF-8 with `errors="replace"`.
- Parse with BeautifulSoup `html.parser`.
- Delete `script`, `style`, `template` and `noscript` elements, and all comments.
- Delete hidden elements: the `hidden` attribute, `aria-hidden="true"`, and inline styles containing `display:none`, `visibility:hidden`, `font-size:0`, `width:0`/`height:0`, or `left:-9999px`.
- Select the main content from `main`, `article`, `[role=main]`, `#content` or `.content`, falling back to `body`.
- Inside it, delete `nav`, `header`, `footer`, `aside`, `form`, `button` and `[role=navigation|banner|contentinfo]`, plus any element whose class or id token matches `^(cookie.*|consent.*|ads?|advert.*|sidebar|breadcrumbs?|toc|table-of-contents)$`.
- Absolutise `href`/`src` against `final_url`.
- Append ` {#id}` to headings that have an `id`.
- Run markdownify with `heading_style="ATX"` and `code_language_callback` reading `language-`/`lang-` classes.

**Markdown path:** remove `<!-- -->` outside fences.

**Both paths:**
- Normalise to LF.
- Outside fences, strip trailing spaces and collapse 3+ blank lines to 2.
- Set flags: `deprecated` if `\b(deprecated|legacy|removed in)\b` (case-insensitive) occurs in the title, any heading or the first 300 body characters. Set `suspect` if the body is shorter than `min_content_chars`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cleaner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: cleaner converts html/markdown to safe versioned pages"
```

---

### Task 4: HTTP infrastructure (safety, robots, rate limit, retries, fetch log, DB schema)

**Files:**
- Create: `src/almanac/db.py`, `src/almanac/fetch/__init__.py`, `fetch/safety.py`, `fetch/ratelimit.py`, `fetch/robots.py`, `fetch/fetchlog.py`, `fetch/http.py`
- Test: `tests/test_db.py`, `tests/test_http.py`

**Interfaces:**
- Consumes: `Settings`, `FetchBlocked` and `FetchFailed` (Task 1).
- Produces:
  - `db.SCHEMA_VERSION = "1"`.
  - `db.connect(path: Path, readonly: bool = False) -> sqlite3.Connection`: creates the parent dir and schema when not read-only, and sets `row_factory = sqlite3.Row`.
    - The schema is DR-03 with the deviations listed in Scope decisions (TEXT source ids, no `query_log`, plus `meta`).
    - `chunk_fts` is created with `tokenize='porter unicode61'` and kept in sync by `AFTER INSERT/DELETE/UPDATE` triggers on `chunk`, using the FTS5 external-content `'delete'` command.
    - `page` has an extra `UNIQUE` column `page_key TEXT`, which holds the string page id (for example `react/19/memo`).
  - `safety.check_url(url: str, allow_private: bool, resolve: Callable[[str], list[str]] = default_resolve) -> None`: raises `FetchBlocked("scheme")` or `FetchBlocked("private-address")`.
  - `RateLimiter(rps: float, clock=time.monotonic, sleep=time.sleep)` with `.wait(host: str)` and `.set_min_interval(host, seconds)`.
  - `RobotsCache(fetch_text: Callable[[str], tuple[int, str]], user_agent: str)` with `.allowed(url) -> bool` and `.crawl_delay(host) -> float | None`:
    - 404 or other 4xx: allow all.
    - 5xx or network error: disallow all (RFC 9309).
  - `FetchLog(conn, run_id: str)` with `.record(url, status: int | None, bytes_: int, ms: int, error: str | None)`.
  - `HttpResult` dataclass: `status`, `final_url`, `body: bytes`, `content_type`, `etag`, `last_modified`.
  - `HttpClient(settings, log: FetchLog, *, transport=None, clock=time.monotonic, sleep=time.sleep, resolve=default_resolve)`:
    - `.get(url, *, etag=None, last_modified=None) -> HttpResult`. A 304 gives `status=304` and an empty body.
    - Raises `FetchBlocked("robots" | "private-address" | "scheme" | "noai")` or `FetchFailed("http-<code>" | "too-large" | "too-many-redirects" | "network: …")`.
    - Every outcome is written to the fetch log.
    - `.user_agent: str`.

- [ ] **Step 1: Write the failing tests** using `httpx.MockTransport`, a fake clock, a recording `sleep`, and a stub `resolve` that maps `docs.test` to `93.184.216.34`.

```python
def test_schema_created_and_versioned(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "1"
    assert {"tech","source","page","chunk","chunk_fts","chunk_vec","symbol","fetch_log"} <= table_names(conn)

def test_rate_limiter_per_host():
    sleeps=[]; t=[0.0]; rl = RateLimiter(1, clock=lambda: t[0], sleep=sleeps.append)
    rl.wait("a"); rl.wait("a"); rl.wait("b")
    assert sleeps == [pytest.approx(1.0)]

def test_robots_disallow_blocks_and_logs(client_factory):   # robots: "User-agent: *\nDisallow: /private"
    c, log_rows = client_factory(robots="User-agent: *\nDisallow: /private")
    with pytest.raises(FetchBlocked, match="robots"): c.get("https://docs.test/private/x")
    assert log_rows()[-1]["error"] == "robots"

def test_robots_5xx_disallows_all(client_factory): ...            # Review Focus 5
def test_robots_404_allows_all(client_factory): ...
def test_crawl_delay_raises_interval(client_factory): ...         # Crawl-delay: 3 → second request sleeps ≈3
def test_429_honours_retry_after(client_factory): ...             # 429 Retry-After: 2, then 200 → sleeps contain 2, status 200
def test_5xx_retried_three_times_then_fails(client_factory): ...  # always 503 → 4 attempts, sleeps [1,2,4], FetchFailed("http-503")
def test_conditional_headers_and_304(client_factory): ...         # sends If-None-Match/If-Modified-Since; 304 → status 304
def test_too_large(client_factory): ...                           # max_page_bytes=10, 11-byte body → FetchFailed("too-large")
def test_redirects_capped_and_final_url(client_factory): ...      # 5 hops ok, final_url recorded; 6 hops → too-many-redirects
def test_redirect_to_private_blocked(client_factory): ...         # 302 → http://169.254.169.254/ → FetchBlocked("private-address")  (Review Focus 5)
def test_scheme_and_private_checks():
    with pytest.raises(FetchBlocked): check_url("ftp://docs.test/x", False, resolve=pub)
    with pytest.raises(FetchBlocked): check_url("http://127.0.0.1/", False, resolve=real)
    with pytest.raises(FetchBlocked): check_url("https://intranet.test/", False, resolve=lambda h: ["10.0.0.5"])
    check_url("http://127.0.0.1/", True, resolve=real)    # allowed when opted in
def test_noai_header_blocks(client_factory): ...                  # X-Robots-Tag: noai → FetchBlocked("noai")
def test_user_agent(client_factory): ...                          # contact "me@example.com" → "Almanac/0.1.0 (+me@example.com)"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_db.py tests/test_http.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `db.py` and the `fetch/` infrastructure modules**

`HttpClient` uses `httpx.Client(follow_redirects=False, timeout=30)` and follows redirects manually so it can run `check_url` on every hop. It streams the body and aborts once the size exceeds `max_page_bytes`. Robots files are fetched once per scheme+host through the same raw path; they are not themselves checked against robots, but they are rate-limited. The backoff delay is `max(retry_after, 2**attempt)` capped at 60 s, with `attempt` starting at 0 (so delays are 1, 2, 4). `Retry-After` may be seconds or an HTTP-date.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_db.py tests/test_http.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: polite, SSRF-safe HTTP client, fetch log and DB schema"
```

---

### Task 5: Web source strategies (llms_txt, sitemap, url_list)

**Files:**
- Create: `src/almanac/fetch/web.py`, `tests/fixtures/site/` (`robots.txt`, `llms.txt`, `llms-full.txt`, `sitemap.xml`, `sitemap-index.xml`, `sitemap-ref.xml`, `reference/memo.html`, `reference/use-memo.md`, `private/secret.html`, `guides/perf.html`)
- Modify: `tests/conftest.py` (add the `docs_site` fixture)
- Test: `tests/test_web_fetch.py`

**Interfaces:**
- Consumes: `HttpClient` and `FetchBlocked`/`FetchFailed` (Task 4), `Manifest` (Task 2), `Source`, `Technology` and `RawDoc` (Task 1).
- Produces:
  - `WebFetcher(http: HttpClient, settings: Settings)`.
  - `.fetch(source: Source, tech: Technology, manifest: Manifest, force: bool = False) -> Iterator[RawDoc]`:
    - Yields only new or modified documents. A 304 is skipped.
    - Per-URL `FetchBlocked`/`FetchFailed` is logged and skipped; failing to fetch the index file itself (llms.txt or sitemap) raises `FetchFailed`.
    - Updates `manifest.set_http_validators` for each 200.
    - Caps at `max_pages_per_source`.
  - The `docs_site` fixture yields `base_url` (for example `http://127.0.0.1:PORT`). It serves `tests/fixtures/site` with a threaded `http.server.SimpleHTTPRequestHandler`, which supports `If-Modified-Since` → 304. Tests that use it set `allow_private_network: True` and `rate_limit_per_host_rps: 100`.

Rules:
- `llms_txt`: when the URL ends with `llms-full.txt`, yield that one file as a single `RawDoc` (`text/markdown`). Otherwise parse every markdown link `[title](url)` in the file, resolve it against the file URL, and fetch it.
- `sitemap`: parse with `defusedxml.ElementTree`, follow a `<sitemapindex>` at most 3 levels deep, and use `<loc>` values.
- `url_list`: fetch `source.urls`.
- Every candidate URL must pass `include` (when non-empty) and must not match `exclude`, using `fnmatch` on the absolute URL.
- Index files (`llms.txt` and sitemaps) are always fetched without conditional headers, so a 304 on the index never hides its links. Only content pages, including a `llms-full.txt` page, use the manifest validators.

- [ ] **Step 1: Write the failing tests**

```python
def test_llms_txt_fetches_links_skips_robots(docs_site, fetcher_for):
    f, manifest, log_rows = fetcher_for(docs_site)
    docs = list(f.fetch(src("llms_txt", f"{docs_site}/llms.txt"), tech, manifest))
    assert sorted(urlparse(d.url).path for d in docs) == ["/reference/memo.html", "/reference/use-memo.md"]
    assert any(r["error"] == "robots" and "/private/" in r["url"] for r in log_rows())

def test_second_fetch_skips_unchanged(docs_site, fetcher_for):
    f, manifest, _ = fetcher_for(docs_site)
    first = list(f.fetch(src("llms_txt", f"{docs_site}/llms.txt"), tech, manifest))
    assert len(first) == 2 and list(f.fetch(src("llms_txt", f"{docs_site}/llms.txt"), tech, manifest)) == []
    assert len(list(f.fetch(src("llms_txt", f"{docs_site}/llms.txt"), tech, manifest, force=True))) == 2

def test_sitemap_index_followed(docs_site, fetcher_for): ...    # sitemap-index.xml → sitemap-ref.xml → memo.html
def test_include_exclude(docs_site, fetcher_for): ...           # exclude ["*/guides/*"] drops perf.html from url_list
def test_page_cap(docs_site, fetcher_for): ...                  # max_pages_per_source=1 → 1 doc
def test_missing_link_logged_others_continue(docs_site, fetcher_for): ...  # llms.txt links /missing.html (404) → still 2 docs, log has 404
def test_llms_full_single_doc(docs_site, fetcher_for): ...      # 1 RawDoc, content_type text/markdown
def test_unreachable_index_raises(fetcher_for): ...             # http://127.0.0.1:9/llms.txt → FetchFailed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_web_fetch.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `WebFetcher`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_web_fetch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: llms.txt, sitemap and url_list fetch strategies"
```

---

### Task 6: Git source

**Files:**
- Create: `src/almanac/fetch/git.py`
- Test: `tests/test_git_fetch.py`

**Interfaces:**
- Consumes: `check_url` (Task 4), `Manifest.source_state`/`set_source_state` (Task 2), `FetchLog` (Task 4) and `RawDoc` (Task 1).
- Produces:
  - `GitFetcher(settings: Settings, cache_dir: Path, log: FetchLog, run_git: Callable = subprocess.run)`.
  - `.fetch(source, tech, manifest, force=False) -> Iterator[RawDoc]`:
    - Each `RawDoc.extra` has `commit_sha` and `repo_path`.
    - `content_type` is `text/markdown` for `.md`, `.mdx` and `.markdown`, and `text/plain` for `.txt` and `.rst`.
    - Other files are ignored.

Rules:
- The URL must be `https`, or a local path or `file:` URL only when `allow_private_network` is set. Otherwise raise `FetchBlocked("scheme")`. For `https`, also run `check_url`.
- First run: `git clone --depth 1 --filter=blob:none --no-checkout [--branch B] URL <cache_dir>/<source_id>`, then `git sparse-checkout set --no-cone <paths…>` (skipped when `paths` is empty), then `git checkout`.
- Later runs: `git fetch --depth 1 origin <branch or HEAD>` then `git reset --hard FETCH_HEAD`.
- Always run with env `GIT_TERMINAL_PROMPT=0` and pass `check=True`. On failure, raise `FetchFailed("git: <stderr first line>")`.
- `commit_sha = git rev-parse HEAD`. If it equals `manifest.source_state(id).get("commit_sha")` and `force` is not set, yield nothing.
- Citation URL: for `github.com/<o>/<r>(.git)`, use `https://github.com/<o>/<r>/blob/<sha>/<path>`. Otherwise use `f"{url.rstrip('/')}/{path}"`.
- Record the SHA in source state only after every document has been yielded.

- [ ] **Step 1: Write the failing tests** (build a real local repo in `tmp_path` with `docs/guide.md`, `docs/img.png` and `src/x.py`)

```python
def test_git_first_fetch_records_sha(local_repo, git_fetcher, manifest):
    docs = list(git_fetcher.fetch(git_src(local_repo, paths=["docs"]), tech, manifest))
    assert [d.extra["repo_path"] for d in docs] == ["docs/guide.md"]
    assert docs[0].extra["commit_sha"] == rev_parse(local_repo)
    assert manifest.source_state("py-git")["commit_sha"] == rev_parse(local_repo)

def test_git_unchanged_then_new_commit(local_repo, git_fetcher, manifest): ...  # 2nd fetch → []; commit change → 1 doc with new sha
def test_git_local_blocked_without_opt_in(local_repo, tmp_path, manifest): ...  # allow_private_network False → FetchBlocked
def test_git_failure_is_fetchfailed(git_fetcher, manifest, tmp_path): ...       # nonexistent path → FetchFailed starting "git:"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_git_fetch.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `GitFetcher`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_git_fetch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: sparse shallow git source with commit SHA tracking"
```

---

### Task 7: Chunker

**Files:**
- Create: `src/almanac/chunker.py`
- Test: `tests/test_chunker.py`

**Interfaces:**
- Consumes: `Page` (Task 1).
- Produces:
  - `estimate_tokens(text: str) -> int` (`ceil(len(text) / 4)`).
  - `Chunk` dataclass: `page_id: str`, `ord: int`, `heading_path: str` (`" > "`-joined, with `{#id}` removed), `anchor: str`, `text: str`, `tokens: int`.
  - `chunk_page(page: Page, target: int = 600, hard_max: int = 900) -> list[Chunk]`.

Algorithm (DR-04):
1. Scan lines and track fences (```` ``` ````/`~~~`). An ATX heading outside a fence starts a new section, and the heading stack produces `heading_path`.
2. The anchor is the explicit `{#id}` when present. Otherwise it is the GitHub-style slug of the heading text: lowercase, characters other than `[a-z0-9 -]` removed, spaces turned into `-`.
3. A section's text includes its heading line. Drop sections whose text without the heading is empty.
4. When a section is at most `target` tokens, it becomes one chunk.
5. Otherwise, split it into blocks: paragraphs separated by blank lines outside fences, and whole fenced blocks. Pack the blocks greedily up to `target`.
   - A block bigger than `target` stands alone.
   - A fenced block bigger than `hard_max` is split at blank lines inside the fence, and each part gets the original opening fence line and a closing fence.
   - A paragraph bigger than `hard_max` is split at sentence ends (`(?<=[.!?])\s+`).
6. Each continuation chunk of a long section starts with the last sentence of the previous chunk's final paragraph, unless that paragraph is code.
7. `ord` runs from 0 in order.

- [ ] **Step 1: Write the failing tests**

```python
def test_heading_paths_and_anchors(make_page):
    body = "# Hooks\n\n## useMemo {#use-memo}\n\nCaches a value.\n\n### Caveats\n\nOnly in components.\n"
    cs = chunk_page(make_page(body=body))
    assert [(c.heading_path, c.anchor) for c in cs] == [("Hooks > useMemo", "use-memo"), ("Hooks > useMemo > Caveats", "caveats")]
    assert cs[0].text.startswith("## useMemo") and [c.ord for c in cs] == [0, 1]

def test_heading_inside_code_is_not_a_heading(make_page): ...   # "```py\n# comment\n```" stays in one chunk
def test_code_block_kept_whole(make_page): ...                  # 400-token fence in 700-token section: some chunk contains the full fence; every chunk has an even count of ```
def test_oversized_code_split_with_fences(make_page): ...       # 1500-token ```py block: each part starts "```py" and ends "```", each ≤ 900 tokens
def test_long_section_respects_hard_max_and_overlaps(make_page):
    paras = [f"Sentence {i} alpha beta gamma delta epsilon. Last of para {i}." for i in range(200)]
    cs = chunk_page(make_page(body="## Long\n\n" + "\n\n".join(paras)))
    assert len(cs) >= 5 and all(c.tokens <= 900 for c in cs)
    assert all(c.heading_path == "Long" for c in cs)
    assert cs[1].text.startswith("Last of para")                 # 1-sentence overlap
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_chunker.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `chunk_page`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_chunker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: heading-aware chunker that keeps code blocks whole"
```

---

### Task 8: Indexer

**Files:**
- Create: `src/almanac/indexer.py`
- Test: `tests/test_indexer.py`

**Interfaces:**
- Consumes: `db.connect` (Task 4), `Store` (Task 2), `chunk_page` (Task 7) and `Config` (Task 1).
- Produces:
  - `IndexStats` dataclass: `pages_indexed`, `pages_skipped`, `pages_deleted`, `chunks_written`.
  - `Indexer(conn, store: Store, config: Config)` with `.index(*, tech: str | None = None, rebuild: bool = False) -> IndexStats`.

Behaviour:
- Everything happens in one `BEGIN IMMEDIATE … COMMIT`. Any exception rolls back, so the previous index stays live (NFR-REL-01/02).
- Upsert `tech` and `source` rows from the config.
- For each stored page (filtered by `tech`), skip it when `page.content_hash` equals the DB value, unless `rebuild` is set.
- Otherwise, delete the old chunks (the triggers keep FTS in sync) and insert the page row (`page_key` = page id, `status='active'`, `flags` as JSON) and its chunks.
- Delete DB pages whose `page_key` is no longer in the store (within the `tech` filter).
- `topics` is `""` in MVP.

- [ ] **Step 1: Write the failing tests**

```python
def test_index_then_skip_unchanged(store_with_two_pages, cfg, tmp_path):
    conn = connect(tmp_path / "a.db"); ix = Indexer(conn, store_with_two_pages, cfg)
    s1 = ix.index(); s2 = ix.index()
    assert (s1.pages_indexed, s2.pages_indexed, s2.pages_skipped) == (2, 0, 2)
    assert fts_count(conn, "memo") > 0

def test_changed_page_replaces_chunks_in_fts(...): ...    # put body without "zebra" after one with it → fts_count("zebra") == 0, pages_indexed == 1
def test_deleted_file_removed_from_index(...): ...        # delete .md file → pages_deleted == 1 and its chunks gone
def test_rebuild_reindexes_all(...): ...                  # rebuild=True → pages_indexed == 2
def test_failure_rolls_back(monkeypatch, ...): ...         # chunk_page raises on 2nd page → page/chunk counts equal pre-run counts
def test_portable_copy_reproduces_index(tmp_path, ...):    # FR-STO-05
    shutil.copytree(docs_a, docs_b); index both into separate DBs
    assert chunk_texts(conn_a) == chunk_texts(conn_b)       # sorted (page_key, ord, text)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_indexer.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `Indexer`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_indexer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: transactional incremental FTS5 indexer"
```

---

### Task 9: Query understanding and retrieval

**Files:**
- Create: `src/almanac/query/__init__.py`, `query/understand.py`, `query/retrieve.py`
- Modify: `tests/conftest.py` (add an `indexed_root` fixture: an almanac root with techs `react` (versions `["19", "18"]`) and `python` (`["3.12"]`), react pages for 18 and 19 (one trust 3, one flagged `deprecated`) and a python page, all stored and indexed)
- Test: `tests/test_understand.py`, `tests/test_retrieve.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `AlmanacError` (Task 1) and the DB schema (Task 4).
- Produces:
  - `Interpretation` dataclass: `tech: str`, `tech_name: str`, `version: str`, `version_source: str` (`"argument"`, `"query"`, `"configured default"` or `"assumed latest"`), `intent: list[str]` (always `["howto"]` in MVP), `summary: str`, `fts_query: str | None`, `warnings: list[dict]`.
  - `map_version(requested: str, indexed: list[str]) -> tuple[str | None, bool]`, returning the chosen version and whether it is a fallback.
  - `build_fts_query(text: str, drop: set[str] = frozenset()) -> str | None`.
  - `understand(query: str, config: Config, indexed: dict[str, list[str]], *, tech: str | None = None, version: str | None = None) -> Interpretation`.
  - `Hit` dataclass: `chunk_id`, `page_id` (page key), `title`, `heading_path`, `anchor`, `text`, `url`, `version`, `kind`, `trust`, `fetched_at`, `flags: list[str]`, `score: float`.
  - `Retriever(conn)`:
    - `.indexed_versions() -> dict[str, list[str]]`
    - `.search(i: Interpretation, k: int = 8, any_version: bool = False) -> list[Hit]`
    - Raises `AlmanacError("E_INDEX_MISSING")` when the `page` table is missing or empty.

Rules:
- **Tech detection:**
  1. If an explicit `tech` is given, it must resolve via `config.tech()`. Otherwise raise `E_UNKNOWN_TECH` with `details={"closest": difflib.get_close_matches(tech, all ids/aliases, n=3)}`.
  2. Otherwise, the techs whose id, name or alias matches the query case-insensitively on word boundaries (`(?<![\w.-])alias(?![\w-])`).
  3. If exactly one matches, use it. If more than one matches, or none matches and more than one is tracked, raise `E_AMBIGUOUS` with `details={"interpretations": [{"tech": id, "example": f"{name}: {query}"}…]}`. If none matches and exactly one is tracked, use that one.
  4. A tech with no indexed versions raises `E_INDEX_MISSING` with the message `No pages indexed for <name>; run: almanac fetch <id> && almanac index`.
- **Version precedence:**
  1. The `version` argument (`"argument"`).
  2. `<alias>[ @]<\d+(\.\d+)*>` in the query (`"query"`).
  3. `tech.default_version()` (`"configured default"`), or `"assumed latest"` when that is `latest`. This also adds `{"code": "VERSION_ASSUMED", "message": "No version evidence; using latest"}`.
- Resolve the chosen version with `map_version`. A fallback adds `{"code": "VERSION_FALLBACK", "message": "Requested <r>; using docs for <v>"}`. `None` raises `E_VERSION_UNAVAILABLE` with `details={"available": indexed}`.
- **`map_version`:**
  1. An exact string match wins.
  2. For `latest`, use `"latest"` if it is indexed, else the highest numeric version.
  3. Parse numbers into int tuples, ignoring a trailing `.x`. An indexed version whose tuple is a prefix of the request is not a fallback; pick the longest such prefix.
  4. Otherwise, the highest older version with the same major is a fallback.
  5. Otherwise, the lowest newer version with the same major is a fallback.
  6. Otherwise, `None`.
- **`build_fts_query`:**
  - Tokens are `[A-Za-z0-9_]+`, lowercased.
  - Drop the stopwords `a an the and or not near of to in on for with how do does did i my me we is are was what why when where which can could should would this that it its be from by as at into get make use using` and anything in `drop` (tech id, name words and aliases).
  - Dedupe preserving order, wrap each token in double quotes and join with ` OR `. Return `None` when no tokens are left.
- **Summary:** `f"{tech_name} {version} ({version_source}): {normalised query[:80]}"`.
- **Retrieval SQL:** `chunk_fts MATCH ?` joined to `chunk` and `page`, filtered on `tech_id`, `version` (unless `any_version`) and `status='active'`, ordered by `bm25(chunk_fts, 1.0, 2.0, 0.5)` with `LIMIT 40`. Then compute `score = -bm25 * (1 + 0.15 * t(trust))`, sort descending and keep `k`. If `fts_query` is `None`, return `[]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_map_version():
    assert map_version("18", ["18", "19"]) == ("18", False)
    assert map_version("18.2", ["18", "19"]) == ("18", False)
    assert map_version("18.3", ["18.2", "19"]) == ("18.2", True)
    assert map_version("18.1", ["18.2", "19"]) == ("18.2", True)
    assert map_version("17", ["18", "19"]) == (None, False)
    assert map_version("latest", ["18", "19"]) == ("19", False)

def test_build_fts_query():
    assert build_fts_query("How do I use memo?") == '"memo"'
    assert build_fts_query("React memo props", drop={"react"}) == '"memo" OR "props"'
    assert build_fts_query("the and of") is None

def test_tech_detection(cfg2, idx):
    assert understand("reactjs list re-render", cfg2, idx).tech == "react"
    with pytest.raises(AlmanacError) as e: understand("react vs python", cfg2, idx)
    assert e.value.code == "E_AMBIGUOUS" and len(e.value.details["interpretations"]) == 2
    with pytest.raises(AlmanacError) as e: understand("list", cfg2, idx)
    assert e.value.code == "E_AMBIGUOUS"
    with pytest.raises(AlmanacError) as e: understand("x", cfg2, idx, tech="reakt")
    assert e.value.code == "E_UNKNOWN_TECH" and "react" in e.value.details["closest"]

def test_version_sources(cfg2, idx): ...   # arg "18" → ("18","argument"); "React 18 memo" → "query"; none → ("19","configured default"); latest-only tech → VERSION_ASSUMED warning; "React 17" → E_VERSION_UNAVAILABLE

def test_retrieve_ranks_and_filters(indexed_root):
    r = Retriever(connect(indexed_root.db, readonly=True))
    hits = r.search(understand("memo props", cfg, r.indexed_versions()))
    assert hits[0].page_id == "react/19/memo" and all(h.version == "19" for h in hits)

def test_trust_breaks_ties(indexed_root): ...      # identical text at trust 1 and trust 3 → trust 1 first
def test_removed_pages_excluded(indexed_root): ... # UPDATE page SET status='removed' → not returned
def test_index_missing(tmp_path): ...              # empty DB → AlmanacError E_INDEX_MISSING

@pytest.mark.parametrize("q", ['"unterminated', "memo*", "-flag", "a:b", "(memo", "NEAR AND OR NOT", "the and of", "😀 ü"])
def test_fts_syntax_never_crashes(indexed_root, q):   # Review Focus 1
    r = Retriever(connect(indexed_root.db, readonly=True))
    assert isinstance(r.search(understand(q, cfg, r.indexed_versions(), tech="react")), list)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_understand.py tests/test_retrieve.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `understand.py` and `retrieve.py`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_understand.py tests/test_retrieve.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: tech/version understanding and trust-boosted FTS retrieval"
```

---

### Task 10: Bundle builder, validator and markdown renderer

**Files:**
- Create: `src/almanac/response/__init__.py`, `response/bundle.py`
- Test: `tests/test_bundle.py`

**Interfaces:**
- Consumes: `Interpretation` and `Hit` (Task 9), `estimate_tokens` (Task 7).
- Produces:
  - `build_bundle(i: Interpretation, hits: list[Hit], *, max_tokens: int, stale_after_days: int, now: datetime, ms: int = 0) -> dict`, which requires `hits` to be non-empty.
  - `not_found(i: Interpretation, nearby_topics: list[str]) -> dict` (SRS §8.1 shape; `suggestions` is fixed to `["Try the official term", "Check that the source is tracked: almanac status"]`; `message` is `f"No indexed source answers this for {tech_name} {version}."`).
  - `validate_bundle(b: dict) -> list[str]`, returning violation messages.
  - `render_markdown(b: dict) -> str`.

Rules:
- `answer = {"text": hits[0].text, "refs": [1]}`, copied verbatim (G-2).
- `excerpts` comes from `hits[1:5]` as `{heading_path, text, refs}`.
- Sources are numbered from 1 in hit order and deduped by `url#anchor`. Each is `{ref, title, kind, trust, version, url: f"{url}#{anchor}", fetched_at}`.
- `interpretation` carries `tech`, `version`, `version_source`, `intent` and `summary`.
- Warnings come from the interpretation, plus:
  - `STALE` when any cited `fetched_at` is older than `now - stale_after_days`, with `refs`.
  - `DEPRECATED` when any cited hit has the `deprecated` flag, with `refs`.
- `meta` holds `tokens`, `ms` and `query_id` (`"q_" + uuid4().hex[:12]`).
- **Budget** (measured as `estimate_tokens(json.dumps(bundle))`):
  1. Drop excerpts from the end, together with sources no longer referenced.
  2. Then cut `answer.text` at the last paragraph boundary that fits. If it is a single paragraph, cut at a sentence end and append ` …`.
  3. Add `{"code": "TRUNCATED", "message": "Cut: excerpts, answer"}`, naming only what was actually cut.
  4. `answer`, `warnings` and `sources` are never removed.
- **Validator (G-1):** `answer` and every excerpt must have non-empty `refs`, every ref must exist in `sources`, and `answer.text` must be non-empty.
- **Markdown order:** `**Interpreted as:** <summary>`, then warnings as `> ⚠ CODE: message`, then `**Answer**` with the text and `[n]` refs, then `**More from the docs**` with the excerpts, then `**Sources**` as `[n] <title>, v<version>, fetched <YYYY-MM-DD>, <url>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_bundle_is_extractive_and_cited(interp, hits):
    b = build_bundle(interp, hits, max_tokens=3000, stale_after_days=14, now=NOW)
    assert b["status"] == "ok" and b["answer"]["text"] == hits[0].text
    assert validate_bundle(b) == []
    assert b["sources"][0]["url"].endswith("#" + hits[0].anchor) and b["sources"][0]["fetched_at"]

def test_stale_and_deprecated_warnings(interp, hits): ...   # hit fetched 20 days before NOW + "deprecated" flag → codes include STALE and DEPRECATED
def test_budget_trims_excerpts_then_answer(interp, big_hits):
    b = build_bundle(interp, big_hits, max_tokens=500, stale_after_days=14, now=NOW)
    assert estimate_tokens(json.dumps(b)) <= 500 and b["answer"]["text"]
    assert any(w["code"] == "TRUNCATED" for w in b["warnings"]) and validate_bundle(b) == []
def test_validator_rejects_uncited():
    assert validate_bundle({"answer": {"text": "x", "refs": [9]}, "excerpts": [], "sources": []})
    assert validate_bundle({"answer": {"text": "x", "refs": []}, "excerpts": [], "sources": []})
def test_not_found_shape(interp): ...                       # keys status/interpretation/message/nearby_topics/suggestions
def test_render_markdown_order(interp, hits): ...           # starts "**Interpreted as:**"; index("**Answer**") < index("**Sources**"); "[1] " present
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_bundle.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `bundle.py`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_bundle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: cited, budgeted bundle builder with grounding validator"
```

---

### Task 11: Read-only query engine

**Files:**
- Create: `src/almanac/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `load_config` (Task 1), `AlmanacPaths` (Task 1), `Store` (Task 2), `connect(readonly=True)` (Task 4), `understand` and `Retriever` (Task 9), and `build_bundle`, `not_found` and `validate_bundle` (Task 10).
- Produces:
  - `Engine(root: Path, *, strict: bool = False)`. It loads the config, then opens the DB read-only once and keeps it open; it does not open it again per call. A missing DB file leads to `E_INDEX_MISSING` at call time, not at construction.
  - `.search_docs(query: str, tech: str | None = None, version: str | None = None, project_path: str | None = None, max_tokens: int | None = None) -> dict`.
  - `.get_page(id: str | None = None, tech: str | None = None, path: str | None = None, version: str | None = None, section: str | None = None) -> dict`, which returns `{status: "ok", id, title, version, source_url, fetched_at, flags, markdown}`.
  - `.list_technologies() -> dict`, which returns `{status: "ok", technologies: [{id, name, versions_indexed, page_count, last_fetched}]}`.
  - `error_response(e: AlmanacError) -> dict`, which returns `{"status": "error", "code", "message", "details"}`.

Rules:
- Every public method catches `AlmanacError` and returns `error_response`. Any other exception is logged with an `error_id` (`uuid4().hex[:8]`) and returns `E_INTERNAL` with the message `Unexpected failure; see logs (error <id>)`, and no traceback.
- **`search_docs`:**
  - `max_tokens` defaults to `settings.response_token_budget`.
  - If `project_path` is given, it must be an existing directory, otherwise `E_BAD_INPUT`. It is otherwise unused in MVP.
  - When there are no hits, return `not_found`, with `nearby_topics` set to up to 3 distinct page titles from `search(any_version=True, k=10)`.
  - Run `validate_bundle`. With `strict`, raise on violations. Otherwise drop the offending excerpts (G-4).
  - Append one JSON line to `paths.query_log` with `at`, `query_id`, `query`, `summary`, `tech`, `version`, `intent`, `hits`, `outcome` (`ok`, `not_found` or the error code) and `ms`. This append-only log is the only write the query path makes.
- **`get_page`:**
  - Resolve the id: either `id`, or `f"{tech}/{version or default}/{path}"`.
  - The id must match `^[a-z0-9][a-z0-9_-]*/[A-Za-z0-9._-]+/[a-z0-9-]+$` with no `..` segment, and `paths.docs.joinpath(id + ".md").resolve()` must be `is_relative_to(paths.docs.resolve())`. Otherwise `E_BAD_INPUT`.
  - A missing page gives `E_NOT_FOUND`.
  - `section` returns only the chunk text(s) whose anchor matches it, or `E_NOT_FOUND`.

- [ ] **Step 1: Write the failing tests**

```python
def test_search_ok_and_logged(indexed_root):
    r = Engine(indexed_root.root).search_docs("memo props", tech="react")
    assert r["status"] == "ok" and r["sources"]
    line = json.loads(indexed_root.paths.query_log.read_text(encoding="utf-8").splitlines()[-1])
    assert line["outcome"] == "ok" and line["tech"] == "react"

def test_not_found_has_nearby_topics(indexed_root): ...   # "quantum flux capacitor" tech=react → status not_found, list nearby_topics
def test_errors_are_structured(indexed_root): ...         # tech="nope" → status error, code E_UNKNOWN_TECH, no "Traceback" in json.dumps(r)
def test_internal_error_hides_trace(indexed_root, monkeypatch): ...  # Retriever.search raises RuntimeError → E_INTERNAL with error id
def test_missing_index(tmp_root_with_config): ...         # no DB → E_INDEX_MISSING

def test_read_only_and_offline(indexed_root, monkeypatch):    # FR-MCP-04, NFR-REL-05
    before = snapshot(indexed_root.paths.docs, indexed_root.paths.db)   # {path: sha256}
    monkeypatch.setattr(socket.socket, "connect", lambda *a: (_ for _ in ()).throw(OSError("net")))
    assert Engine(indexed_root.root).search_docs("memo", tech="react")["status"] == "ok"
    assert snapshot(indexed_root.paths.docs, indexed_root.paths.db) == before

def test_get_page_by_id_and_section(indexed_root): ...    # id react/19/memo → markdown contains "memo"; section="caveats" → only that section

@pytest.mark.parametrize("bad", ["../almanac", "react/19/../../almanac", "/etc/passwd", "C:\\Windows\\win", "react\\19\\memo", "react/19/memo.md"])
def test_get_page_rejects_escape(indexed_root, bad):      # Review Focus 4
    assert Engine(indexed_root.root).get_page(id=bad)["code"] == "E_BAD_INPUT"

def test_get_page_symlink_escape(indexed_root, tmp_path): ...   # skip if os.symlink raises OSError (Windows without dev mode)
def test_list_technologies(indexed_root): ...                   # react versions_indexed == ["18","19"], page_count > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_engine.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `Engine`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: read-only query engine with structured errors and query log"
```

---

### Task 12: MCP server (stdio)

**Files:**
- Create: `src/almanac/mcp_server.py`, `src/almanac/logs.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `Engine` (Task 11).
- Produces:
  - `logs.configure(paths: AlmanacPaths, run_id: str, level: str = "INFO", stderr: bool = False) -> logging.Logger`. It writes JSON lines with `ts`, `level`, `run_id`, `module`, `msg` and optional `duration_ms` to `paths.log_file`. It never adds a stdout handler.
  - `create_server(root: Path) -> FastMCP`, which registers the tools `search_docs`, `get_page` and `list_technologies`.
  - `main(argv: list[str] | None = None) -> None`, which parses `--dir` and `--log-level`, configures logging and calls `server.run("stdio")`. `python -m almanac.mcp_server --dir X` works.

Tool contracts:
- **`search_docs`:** `query: str`, `tech`, `version`, `project_path` (optional `str`), `max_tokens: int = 3000`. The handler checks `3 <= len(query) <= 2000` and `500 <= max_tokens <= 12000`, and returns `E_BAD_INPUT` otherwise.
- **`get_page`:** the same fields as `Engine.get_page`.
- **`list_technologies`:** no arguments.

Each tool returns the engine's dict.

Tool descriptions, used verbatim:
- `search_docs`: "Search current, version-correct official documentation tracked by Almanac. Call this BEFORE writing or changing code that uses a library, framework or tool, and whenever you are unsure an API exists or how it behaves. Pass a plain-English description of what you need; pass project_path when known. Returns a cited answer with sources; if status is not_found, say so instead of guessing."
- `get_page`: "Read the full cleaned documentation page (or one section by anchor) for a search_docs hit when the excerpt is not enough."
- `list_technologies`: "List technologies and versions Almanac has indexed, with page counts and freshness. Call to check coverage before relying on Almanac for a technology."

- [ ] **Step 1: Write the failing tests**

```python
async def test_tools_listed_with_schemas(indexed_root):
    srv = create_server(indexed_root.root)
    tools = {t.name: t for t in await srv.list_tools()}
    assert set(tools) == {"search_docs", "get_page", "list_technologies"}
    assert tools["search_docs"].inputSchema["required"] == ["query"]
    assert "BEFORE writing or changing code" in tools["search_docs"].description

async def test_bad_input(indexed_root): ...   # query "hi" and max_tokens 100 → code E_BAD_INPUT

async def test_stdio_session_end_to_end(indexed_root):   # Review Focus 2
    params = StdioServerParameters(command=sys.executable, args=["-m", "almanac.mcp_server", "--dir", str(indexed_root.root)])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await asyncio.wait_for(s.initialize(), 10)
        res = await s.call_tool("search_docs", {"query": "memo props", "tech": "react"})
        assert json.loads(res.content[0].text)["status"] == "ok"
```
Add `pytest-asyncio` (or `anyio`'s pytest plugin) as a dev dependency and mark these tests async.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `logs.py` and `mcp_server.py`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: stdio MCP server exposing search_docs, get_page, list_technologies"
```

---

### Task 13: Build pipeline and CLI `init`, `add`, `fetch`, `index`

**Files:**
- Create: `src/almanac/pipeline.py`, `src/almanac/cli.py`
- Modify: `src/almanac/__main__.py` (`from almanac.cli import app; app()`)
- Test: `tests/test_pipeline.py`, `tests/test_cli_build.py`

**Interfaces:**
- Consumes: every build-side unit (Tasks 1–8) and `logs.configure` (Task 12).
- Produces:
  - `FetchReport` dataclass: `ok: list[str]`, `failed: dict[str, str]`, `skipped: dict[str, str]`, `changes: dict[ChangeKind, int]`.
  - `run_fetch(root: Path, *, tech: str | None = None, source: str | None = None, force: bool = False, dry_run: bool = False) -> FetchReport`.
  - `run_index(root: Path, *, tech: str | None = None, rebuild: bool = False) -> IndexStats`.
  - `cli.app` (a Typer app).
  - Every command takes `--dir` (default: the env var `ALMANAC_DIR`, else the current working directory) and `--json`.

`run_fetch` rules:
- Loop over the enabled sources of the selected techs. Use `WebFetcher` for `llms_txt`, `sitemap` and `url_list`, and `GitFetcher` for `git`. `feed` and `crawl` go into `skipped` with `"not supported until v1.0"`.
- For each `RawDoc`: build the page id with `store.make_page_id(tech, detect_version(...), final_url)`, then run `clean`, then `store.put`.
- Any exception from one source records `failed[source_id] = reason` and the loop continues (FR-SCH-05).
- Save the manifest once at the end, and also after each source so a crash keeps earlier progress.
- `dry_run` lists sources without any network access.

CLI behaviour:

| Command | Behaviour | Exit |
| --- | --- | --- |
| `init [--model M]` | Creates `almanac.yaml` (settings defaults plus a commented example tech), `glossary.yaml` (`terms: {}`), and the folders `docs`, `data`, `golden`, `reports` and `logs`. If git is on PATH and the directory is not already a repo, runs `git init` and writes a `.gitignore` containing `data/` and `logs/`. An existing `almanac.yaml` is left untouched, with the message "already initialised". | 0; 3 if the directory is not writable |
| `add <tech> --source URL [--type T] [--kind K] [--trust N] [--name NAME] [--yes]` | Infers the type: `*llms.txt` or `*llms-full.txt` → `llms_txt`; `*sitemap*.xml` → `sitemap`; `*.git` or `github.com/<o>/<r>` → `git`; anything else → `url_list`. Creates the tech (name defaults to the title-cased id) or appends to it. Source id is `f"{tech}-{type}"`, with `-2`, `-3` … if taken. Edits with ruamel round-trip so comments are preserved, then re-validates via `load_config`. | 0; 2 for invalid input or duplicates; 4 with no `--source` ("Source discovery arrives in v1.0; pass --source URL") |
| `fetch [tech] [--source ID] [--force] [--dry-run]` | `run_fetch`, then a summary line per source. | 0; 5 if some sources failed; 6 if every attempted source failed |
| `index [--rebuild] [--tech T]` | `run_index` and prints the stats. | 0; 3 for a DB or IO error |

On any command, an invalid config prints `ConfigError` plus "Fix the field above in almanac.yaml and rerun." to stderr and exits 2 (NFR-USE-02).

- [ ] **Step 1: Write the failing tests** (Typer `CliRunner`, `docs_site`, and a helper `write_config(root, base_url)` that sets `allow_private_network: true` and `rate_limit_per_host_rps: 100`)

```python
def test_init_creates_layout(tmp_path):
    res = runner.invoke(app, ["init", "--dir", str(tmp_path)])
    assert res.exit_code == 0
    assert all((tmp_path / p).exists() for p in ["almanac.yaml", "glossary.yaml", "docs", "data", "golden", "reports", "logs"])
    load_config(tmp_path / "almanac.yaml")                 # template is valid
    assert runner.invoke(app, ["init", "--dir", str(tmp_path)]).exit_code == 0   # idempotent

def test_add_infers_type_and_keeps_comments(tmp_root): ...  # add react --source https://react.dev/llms.txt → type llms_txt; "# " comment lines still in file
def test_add_without_source_exit_4(tmp_root): ...
def test_add_duplicate_exit_2(tmp_root): ...                # same URL/id twice under explicit --type with an existing source id clash
def test_invalid_config_exit_2_with_location(tmp_root): ... # trust: 9 → exit 2, stderr contains "almanac.yaml:" and "trust"

def test_fetch_isolates_failures(docs_site, tmp_root):      # Review Focus 5 / FR-SCH-05
    write_config(tmp_root, docs_site, extra_sources=[{"id": "dead", "type": "url_list", "urls": ["http://127.0.0.1:9/x"], "kind": "guide", "trust": 1},
                                                     {"id": "rel", "type": "feed", "url": f"{docs_site}/r.atom", "kind": "changelog", "trust": 1}])
    rep = run_fetch(tmp_root)
    assert "react-docs" in rep.ok and "dead" in rep.failed and rep.skipped["rel"] == "not supported until v1.0"
    assert runner.invoke(app, ["fetch", "--dir", str(tmp_root)]).exit_code == 5

def test_fetch_total_failure_exit_6(tmp_root): ...          # only the dead source → exit 6
def test_fetch_then_index_json(docs_site, tmp_root): ...    # fetch exit 0; `index --json` → pages_indexed ≥ 2; second `index --json` → pages_indexed 0
def test_fetch_dry_run_no_network(tmp_root, monkeypatch): ...  # socket connect patched to raise → exit 0, lists source ids
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_pipeline.py tests/test_cli_build.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `pipeline.py` and the four commands in `cli.py`**

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_pipeline.py tests/test_cli_build.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: fetch/index pipeline with failure isolation and init/add/fetch/index CLI"
```

---

### Task 14: CLI `search`, `serve`, `status`, end-to-end test and setup docs

**Files:**
- Modify: `src/almanac/cli.py`
- Create: `README.md`, `tests/test_e2e.py`, `tests/fixtures/site/reference/unicode.html`
- Test: `tests/test_cli_query.py`, `tests/test_e2e.py`

**Interfaces:**
- Consumes: `Engine` and `render_markdown` (Tasks 10–11), `mcp_server.main` (Task 12), and `run_fetch`/`run_index` (Task 13).
- Produces the following CLI commands:

| Command | Behaviour | Exit |
| --- | --- | --- |
| `search "<q>" [--tech] [--version] [--max-tokens N] [--json]` | Calls `Engine.search_docs`. Prints `render_markdown`, or the JSON with `--json`. A `not_found` prints its message and `nearby_topics`. | 0 ok; 8 for `not_found`; 3 for `E_INDEX_MISSING`; 2 for any other error code |
| `serve [--log-level L]` | `mcp_server.main(["--dir", root, "--log-level", L])` | runs until stopped |
| `status [--json]` | Per tech: indexed versions and page count. Per source: last fetch time, last status and error count, from `fetch_log` grouped by the source's URL prefix. Also chunk count and DB size in bytes. | 0 |

`README.md` must contain:
- Install: `uv tool install .`
- The PRD §7.1 setup sequence using the real commands.
- The registration command `claude mcp add almanac -- almanac serve --dir <path>`.
- The SRS §6.4 `CLAUDE.md` snippet, verbatim.
- The MVP limits from Scope decisions.

- [ ] **Step 1: Write the failing tests**

```python
def test_e2e_fetch_index_search(docs_site, tmp_root):    # PRD MVP exit condition, automated half
    write_config(tmp_root, docs_site)
    assert runner.invoke(app, ["fetch", "--dir", str(tmp_root)]).exit_code == 0
    assert runner.invoke(app, ["index", "--dir", str(tmp_root)]).exit_code == 0
    res = runner.invoke(app, ["search", "how do I skip re-rendering with memo", "--dir", str(tmp_root), "--json"])
    b = json.loads(res.stdout)
    assert res.exit_code == 0 and b["status"] == "ok"
    assert b["sources"][0]["url"].startswith(docs_site) and validate_bundle(b) == []

def test_e2e_not_found_exit_8(...): ...                  # "quantum flux capacitor configuration" → exit 8
def test_e2e_unicode_page(...): ...                      # unicode.html titled "Pfeile → ü 日本" → search "Pfeile" --json answer contains "日本" (Review Focus 3)
def test_search_markdown_output(...): ...                # without --json: stdout starts "**Interpreted as:**"
def test_search_index_missing_exit_3(tmp_root): ...
def test_status_json(...): ...                           # keys technologies[*].page_count, sources[*].last_status, index.chunks, index.db_bytes
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_query.py tests/test_e2e.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the commands and write `README.md`**

- [ ] **Step 4: Run the whole suite and lint**

Run: `uv run pytest -v && uv run ruff check src tests`
Expected: all tests pass, ruff reports no errors.

- [ ] **Step 5: Manual MVP gate (PRD §6.2 exit condition)**

1. In a scratch directory, run `almanac init` and `almanac add <tech> --source <real llms.txt or repo>` for 2–3 technologies chosen by the user (PRD Q1).
2. Run `almanac fetch && almanac index`, then `claude mcp add almanac -- almanac serve --dir <dir>`.
3. In Claude Code, `/mcp` lists the three tools.
4. Asking a real question produces a `search_docs` call whose answer cites stored docs.

Record the question and the result in the PR description.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: search/serve/status CLI, end-to-end test and setup guide"
```

---

## Follow-up plans (not in this plan)

Write one plan per v1.0 milestone after the MVP gate passes:
- M4: embeddings and hybrid RRF retrieval, with the golden-set baseline.
- M5: intent classification, glossary and rewriting.
- M6: project version detection from manifests and lockfiles.
- M7: the full bundle sections, confidence thresholds and conflicts.
- M8: feed and changelog ingestion, deprecations, `whats_new`, `best_practices` and `lookup_symbol`.
- M9: `refresh --due`, locks, digests, `removed` marking and git auto-commit of the store.
- M10: `almanac test`, `doctor`, the weekly report, crawl and discovery for `add`, and the security review.
