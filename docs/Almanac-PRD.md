# Almanac: Product Requirements Document (PRD)

2026-11-24

## 1. Overview and vision

Almanac is a local-first knowledge base that gives Claude Code current, cited, version-correct documentation and best practices on demand, so code is written against what a technology does today, not what a model remembers.

**Pitch.** Developers using AI coding assistants lose time to outdated APIs, invented function names and advice that ignores the version in their project. Almanac fetches official documentation, release notes and best-practice sources for the technologies you choose, refreshes them on a schedule, and exposes them to Claude Code through a Model Context Protocol (MCP) server. You ask in plain English. Almanac works out the technology and version, searches, and returns an answer with sources, pitfalls, best practices and related topics.

**Document status.** Version 0.1, draft, 24 Nov 2026. The companion SRS holds the detailed technical requirements.

### Product summary

| Item           | Value                                                             |
|----------------|-------------------------------------------------------------------|
| Product name   | Almanac                                                           |
| Type           | Developer tool: CLI, MCP server and scheduled refresher           |
| Primary client | Claude Code (terminal and VS Code)                                |
| Subscription   | Claude Pro; no separate paid API needed                           |
| Hosting        | Local machine, single user (v1)                                   |
| Storage        | Markdown files plus a SQLite index                                |
| Core promise   | Latest, cited, version-correct answers to plain-English questions |

### Product principles

1.  **Grounded or silent.** Every claim traces to a stored source. No source means "not found", never a guess.

2.  **Version-aware.** Answers match the versions in the user's own project.

3.  **Plain English in, structured answer out.** No need to know API names.

4.  **Local-first.** Data, index and queries stay on the user's machine by default.

5.  **Fresh by default.** Scheduled refresh, with an "as of" date on every answer.

6.  **Small and boring.** Files, SQLite and one process. Nothing to babysit.

7.  **Transparent.** Always show how the question was interpreted and which sources ranked.

## 2. Problem statement

AI coding assistants write code from training knowledge and generic web results, so developers pay for it in rework when APIs have changed, versions differ or advice is out of date.

### 2.1 Pain points

1.  **Knowledge cutoff.** Libraries release continuously. The model may recommend deprecated or removed APIs with full confidence.

2.  **Version blindness.** Advice for React 17 is applied to a React 19 project, or the reverse. Defaults, signatures and recommended patterns differ between versions.

3.  **Invented APIs.** When unsure, the model may produce plausible function names or options that do not exist.

4.  **Best-practice drift.** Recommended patterns change (new data-fetching models, new security guidance). Old habits persist in generated code.

5.  **Manual lookup cost.** The developer leaves the editor, finds the right page, copies text back into the session, and repeats this for every question.

6.  **No audit trail.** Answers rarely say where they came from, so correctness is hard to check quickly.

7.  **Query friction.** Official docs are organised by API name. Developers think in outcomes ("stop this list re-rendering").

### 2.2 Current alternatives and their gaps

| Alternative                        | Strength                                                                                   | Gap for this user                                                                                                                            |
|------------------------------------|--------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| Model knowledge only               | Instant, no setup                                                                          | Stale, unversioned, uncited                                                                                                                  |
| Manual copy-paste of docs          | Accurate when done carefully                                                               | Slow, breaks flow, not repeatable                                                                                                            |
| Live web search inside the session | Fresh                                                                                      | Noisy, mixed source quality, no version pinning, repeated cost per question                                                                  |
| Hosted library-docs MCP services   | Easy setup, broad coverage                                                                 | Third-party coverage and freshness; limited control over sources; usually no best-practice or changelog layer; query data leaves the machine |
| **Almanac**                        | Own sources, own schedule, version-aware, cited, best practices and changelog layer, local | Needs initial setup and source curation                                                                                                      |

Hosted services are worth evaluating in parallel. Almanac is built for the cases they do not cover: chosen sources, best-practice synthesis, private or internal docs later, and full control.

### 2.3 Opportunity

Claude Code can call external tools through MCP. A purpose-built docs tool turns it from "answers from memory" into "answers from the current official source, with a citation". The cost is a one-time setup and a small local service.

## 3. Goals, non-goals and success metrics

Almanac succeeds if Claude Code answers technical questions from current, cited sources, and the developer rarely has to correct an outdated or invented API.

### 3.1 Goals

| ID  | Goal                    | Outcome                                                                                       |
|-----|-------------------------|-----------------------------------------------------------------------------------------------|
| G1  | Fresh knowledge         | Docs and changelogs for chosen technologies refresh automatically and show an as-of date      |
| G2  | Accuracy                | Every answer is grounded in stored sources and cites them; unknowns are reported, not guessed |
| G3  | Version correctness     | Answers match the version used in the current project                                         |
| G4  | Natural questions       | Plain-English questions find the right API without knowing its name                           |
| G5  | Richer answers          | Each answer adds best practices, gotchas, related topics, an example and recent changes       |
| G6  | Seamless in Claude Code | Claude calls Almanac by itself; the developer adds no manual steps per question               |
| G7  | Low upkeep              | Adding a technology takes minutes; running it takes no attention                              |
| G8  | Local and private       | Works offline for queries; no query data leaves the machine                                   |

### 3.2 Non-goals (v1)

- Hosting Almanac as a shared team or public service

- A graphical web interface

- Replacing the official documentation sites

- Indexing paywalled, login-gated or private sources (planned later for internal docs)

- Generating or modifying project code by itself; Almanac only supplies knowledge

- Guaranteeing that source documentation is itself correct

- Supporting every technology; coverage grows by user choice

### 3.3 Success metrics

Targets are proposed starting points and should be reviewed after the first month of use.

| Metric              | Definition                                                                      | Target                     | Measured by                             |
|---------------------|---------------------------------------------------------------------------------|----------------------------|-----------------------------------------|
| Citation coverage   | Share of answer claims with a source link                                       | 100%                       | Response validator in tests and runtime |
| Retrieval hit rate  | Golden-set questions whose correct chunk appears in top 3                       | 90% or higher at v1.0      | `almanac test`                          |
| Version match       | Answers using the project's detected version, where detectable                  | 98% or higher              | Golden set with fixtures                |
| Query latency       | Time from MCP call to response, local, warm                                     | p95 under 1.5 s            | Built-in timing log                     |
| Freshness           | Pages refreshed within their configured interval                                | 95% or higher              | Health report                           |
| Fetch health        | Sources fetched without error in a refresh run                                  | 97% or higher              | Health report                           |
| Agent adoption      | Sessions where Claude Code calls Almanac before using an API it was asked about | 80% or higher              | Query log review, weekly                |
| Zero-result honesty | Unanswerable golden questions that return a clear "not found"                   | 100%                       | `almanac test`                          |
| Setup time          | Install to first successful query for one technology                            | under 15 minutes           | Manual timing at release                |
| Rework reduction    | Self-reported count of outdated-API corrections per week                        | Falling trend over 8 weeks | Personal log                            |

## 4. Target users and personas

The primary user is one working developer who codes with Claude Code and wants answers that are current and checkable.

### 4.1 Personas

| Persona                             | Profile                                                                                                 | Goals                                                                                               | Frustrations                                                                                                         |
|-------------------------------------|---------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| **Working developer** (primary)     | Writes code daily in terminal and VS Code; on Claude Pro; works across several frameworks and languages | Ship correct code fast; stay current without reading every changelog; trust what the assistant says | Outdated or invented APIs; version mismatches; switching tabs to verify; vague "best practice" advice with no source |
| **Claude Code** (agent consumer)    | Calls MCP tools during coding sessions; has limited context and token budget                            | Get small, precise, cited chunks; know when to stop searching; learn what changed recently          | Long noisy results; no version information; no signal on source quality                                              |
| **Maintainer** (same person, later) | Curates sources, tunes quality, upgrades components                                                     | Add a technology quickly; see what broke; keep tests green                                          | Silent fetch failures; opaque ranking; slow rebuilds                                                                 |
| **Team lead** (future)              | Wants the whole team on the same knowledge base                                                         | Shared sources, shared standards, internal docs                                                     | Not in v1                                                                                                            |

### 4.2 A day with Almanac

1.  **Morning.** A scheduled refresh ran overnight. The change digest says a framework released a minor version and deprecated one option the project uses.

2.  **Coding.** The developer asks Claude Code, "make this table update without reloading the whole page." Claude calls Almanac, which reads the project's framework version, finds the right API and returns the official approach, a best-practice note, one known pitfall and a link.

3.  **Debugging.** A pasted error goes to Almanac, which matches it to the relevant doc section and a recent changelog entry explaining the cause.

4.  **Review.** Before a PR, the developer asks, "is anything I used deprecated?" Almanac checks the symbols against changelog and deprecation flags.

5.  **Weekly.** The quality report lists three questions that returned weak results. The developer adds a glossary entry and one source.

## 5. User stories and use cases

Stories are grouped into six epics. Priority uses MoSCoW: M must, S should, C could.

### 5.1 Stories

| ID    | Epic        | Story                                                                                      | Acceptance summary                                             | Pri |
|-------|-------------|--------------------------------------------------------------------------------------------|----------------------------------------------------------------|-----|
| US-01 | Setup       | As a developer, I install Almanac and register it with Claude Code in one command sequence | `init` and `serve` work; Claude Code lists the Almanac tools   | M   |
| US-02 | Setup       | I add a technology by name                                                                 | Candidates discovered; I confirm; first fetch runs             | M   |
| US-03 | Setup       | I define sources and trust tiers in a config file                                          | Invalid config gives a precise error                           | M   |
| US-04 | Setup       | I pin or follow versions per technology                                                    | Docs for each requested version are stored separately          | M   |
| US-05 | Asking      | I ask in plain English without knowing API names                                           | Correct page in top 3 for golden questions                     | M   |
| US-06 | Asking      | I paste an error and get the likely cause and fix                                          | Error code and symbols extracted; relevant chunks returned     | S   |
| US-07 | Asking      | I ask "how should I do X" and get best practices                                           | Best-practice section populated from tagged sources            | M   |
| US-08 | Asking      | I ask "what changed in X since version Y"                                                  | Changelog entries listed newest first                          | S   |
| US-09 | Asking      | I ask for a comparison of two approaches                                                   | Both approaches described with sources and trade-offs          | C   |
| US-10 | Asking      | I ask "is this API deprecated?"                                                            | Deprecation status with source and replacement                 | S   |
| US-11 | Accuracy    | I see how my question was interpreted                                                      | One-line interpretation shown first                            | M   |
| US-12 | Accuracy    | Every statement has a source link, version and date                                        | 100% citation coverage in tests                                | M   |
| US-13 | Accuracy    | I am told when nothing was found                                                           | Structured "not found" with nearby topics; no invented content | M   |
| US-14 | Accuracy    | I am warned about stale or fallback-version answers                                        | Warning with page age or version mapping                       | M   |
| US-15 | Accuracy    | Answers use my project's version automatically                                             | Version read from manifest or lockfile                         | M   |
| US-16 | Freshness   | Docs refresh on a schedule without my attention                                            | `refresh --due` runs from cron or timer                        | M   |
| US-17 | Freshness   | I get a digest of what changed                                                             | Markdown digest per run                                        | S   |
| US-18 | Freshness   | One broken site does not stop others                                                       | Failures isolated and retried                                  | M   |
| US-19 | Integration | Claude Code uses Almanac on its own                                                        | Project instructions and tool descriptions trigger calls       | M   |
| US-20 | Integration | Responses are compact                                                                      | Default under 3,000 tokens; adjustable                         | M   |
| US-21 | Integration | Queries work offline                                                                       | No network use at query time                                   | M   |
| US-22 | Quality     | I run a test set of my own questions                                                       | `almanac test` reports score and failures                      | M   |
| US-23 | Quality     | I see which queries failed                                                                 | Weekly report of weak and empty queries                        | S   |
| US-24 | Quality     | I extend a glossary of my own terms                                                        | Glossary changes apply without rebuild                         | S   |
| US-25 | Quality     | I check system health                                                                      | `doctor` reports config, index, models, MCP registration       | S   |

### 5.2 Key use cases

| Use case                     | Trigger                                                      | Main flow                                                                                                           | Result                                      |
|------------------------------|--------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|---------------------------------------------|
| UC-1 Implement a feature     | Developer asks Claude Code to build something with a library | Claude calls `search_docs`; Almanac detects tech and version; returns cited answer with best practices and pitfalls | Code written against current documented API |
| UC-2 Fix an error            | Error pasted into the session                                | Claude calls `explain_error`; Almanac extracts symbols, finds docs and changelog matches                            | Cause and documented fix with links         |
| UC-3 Upgrade a dependency    | Developer plans to bump a version                            | Claude calls `whats_new` with the two versions                                                                      | Breaking changes and migration notes listed |
| UC-4 Review for deprecations | Before a pull request                                        | Claude calls `check_version` and `search_docs` for used symbols                                                     | Deprecated or removed APIs flagged          |
| UC-5 Learn a new tool        | Developer unfamiliar with a technology                       | Claude calls `best_practices` and `search_docs`                                                                     | Structured starter guidance with sources    |
| UC-6 Add a technology        | Project adopts a new library                                 | `almanac add`, confirm sources, run fetch and index                                                                 | Tool available in minutes                   |

## 6. Feature requirements

The MVP proves the core loop: fetch, index, ask in plain English, get a cited answer inside Claude Code. v1.0 adds accuracy and freshness hardening. Later releases extend reach.

### 6.1 Feature list

| ID   | Feature                  | Description                                                              | Release | MoSCoW | Stories      |
|------|--------------------------|--------------------------------------------------------------------------|---------|--------|--------------|
| F-01 | Source registry          | YAML config of technologies, sources, trust tiers, versions              | MVP     | Must   | US-03, US-04 |
| F-02 | Fetcher                  | llms.txt, git, sitemap and crawl fetch with politeness rules             | MVP     | Must   | US-02, US-18 |
| F-03 | Markdown store           | Clean markdown with front-matter and version folders                     | MVP     | Must   | US-04        |
| F-04 | Keyword index            | SQLite FTS5 over heading-based chunks                                    | MVP     | Must   | US-05        |
| F-05 | MCP server (core tools)  | `search_docs`, `get_page`, `list_technologies` over stdio                | MVP     | Must   | US-01, US-19 |
| F-06 | Citations                | Source URL, version and date on every statement                          | MVP     | Must   | US-12        |
| F-07 | CLI                      | init, add, fetch, index, search, serve, status                           | MVP     | Must   | US-01, US-02 |
| F-08 | Semantic search          | Local embeddings and hybrid ranking                                      | v1.0    | Must   | US-05        |
| F-09 | Query understanding      | Tech and intent detection, rewriting, glossary, interpretation line      | v1.0    | Must   | US-05, US-11 |
| F-10 | Version detection        | Read project manifests and lockfiles; map to doc versions                | v1.0    | Must   | US-15        |
| F-11 | Response bundle          | Answer, best practices, gotchas, related, example, what's new            | v1.0    | Must   | US-07, US-08 |
| F-12 | Changelog layer          | Release feeds and deprecation detection                                  | v1.0    | Must   | US-08, US-10 |
| F-13 | Best-practice layer      | Tagged best-practice sources and `best_practices` tool                   | v1.0    | Must   | US-07        |
| F-14 | Honest failure           | "Not found" shape, confidence threshold, staleness and fallback warnings | v1.0    | Must   | US-13, US-14 |
| F-15 | Scheduler and digests    | `refresh --due`, locks, change digest, setup recipes                     | v1.0    | Must   | US-16, US-17 |
| F-16 | Golden-set tests         | `almanac test` with scoring and regression report                        | v1.0    | Must   | US-22        |
| F-17 | Error explainer          | `explain_error` tool with error parsing                                  | v1.0    | Should | US-06        |
| F-18 | Health and weekly report | Doctor command, weak-query and broken-link reports                       | v1.0    | Should | US-23, US-25 |
| F-19 | Symbol lookup            | Exact API-name lookup and deprecation check                              | v1.0    | Should | US-10        |
| F-20 | Source discovery         | `add` suggests llms.txt, sitemap and repos                               | v1.0    | Should | US-02        |
| F-21 | Comparison mode          | Side-by-side approaches with trade-offs                                  | v1.1    | Could  | US-09        |
| F-22 | Re-ranker model          | Local cross-encoder re-ranking                                           | v1.1    | Could  | US-05        |
| F-23 | Headless rendering       | Fetch JavaScript-rendered docs                                           | v1.1    | Could  | US-02        |
| F-24 | Private docs             | Index local folders and internal wikis                                   | v2      | Could  | not in v1    |
| F-25 | Team mode                | Shared index, access control, hosted option                              | v2      | Could  | not in v1    |

### 6.2 Scope by release

| Release    | Theme                             | Exit condition                                                                            |
|------------|-----------------------------------|-------------------------------------------------------------------------------------------|
| MVP (v0.1) | Core loop for 2 to 3 technologies | A real question in Claude Code returns a cited answer from stored docs                    |
| v1.0       | Accurate, fresh, natural          | Success metrics in section 3.3 met on the golden set; refresh runs unattended for 2 weeks |
| v1.1       | Depth                             | Comparison mode and better ranking; 10 or more technologies                               |
| v2         | Reach                             | Private docs and team sharing                                                             |

## 7. User experience

The developer sets Almanac up once, then mostly talks to Claude Code. Almanac's interface is therefore designed for two readers: a human at the command line and an AI agent calling tools.

### 7.1 Setup flow

``` bash
pip install almanac-docs          # package name to be confirmed
almanac init                      # creates almanac.yaml, folders, downloads embedding model
almanac add react                 # discovers sources, asks to confirm
almanac fetch && almanac index    # first build
claude mcp add almanac -- almanac serve
almanac doctor                    # verifies everything
```

After this, Claude Code lists the Almanac tools. A short instruction in the project's `CLAUDE.md` tells Claude when to use them (see SRS section 6.4).

### 7.2 MCP tools as Claude sees them

| Tool                | Use it when                                                                         | Returns                                                                                        |
|---------------------|-------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| `search_docs`       | Any question about how a technology works, how to do something, or which API to use | Bundle: interpretation, answer, best practices, gotchas, related, example, what's new, sources |
| `best_practices`    | "How should I structure or approach X?"                                             | Recommended patterns with rationale and sources                                                |
| `explain_error`     | An error message or stack trace is in hand                                          | Probable cause, documented fix, related changes                                                |
| `whats_new`         | Upgrading, or checking recent changes                                               | Changelog entries, breaking changes, deprecations                                              |
| `check_version`     | Before relying on version-specific behaviour                                        | Detected project versions and the doc versions used                                            |
| `get_page`          | A search hit needs full reading                                                     | Full cleaned page or section                                                                   |
| `lookup_symbol`     | An exact API name needs checking                                                    | Signature, status, replacement if deprecated                                                   |
| `list_technologies` | Deciding whether Almanac covers something                                           | Tracked technologies, versions, freshness                                                      |

### 7.3 Example interaction

The developer asks Claude Code: "my React list keeps re-rendering when I type in the search box, how do I fix it?"

Claude calls `search_docs`. The response, shown here in simplified form with placeholder links:

``` markdown
**Interpreted as:** React 19 (from package-lock.json), performance, preventing unnecessary re-renders

**Answer**
Move the changing input state into its own component, or wrap the list in `memo` so it
re-renders only when its props change. [1][2]

**Best practices**
- Measure first with the profiler before adding memoisation. [3]
- Keep state as close as possible to where it is used. [2]

**Gotchas**
- `memo` compares props shallowly; new object or function props defeat it. [1]
- A change in React 19 affects how this behaves with the compiler enabled. [4]

**Example (from docs)**
  ...official snippet... [1]

**Related:** useMemo, useCallback, key prop, transitions
**What's new:** 19.1 changed ... [4]

**Sources**
[1] Reference: memo, v19, fetched 2026-11-23, https://example.org/...
[2] Guide: Thinking in React, v19, fetched 2026-11-23, https://example.org/...
...
```

### 7.4 Interface principles

- **Interpretation first.** The first line states what Almanac understood, so a wrong guess is visible at once.

- **Compact by default.** Short sections, no filler, a token budget. A `max_tokens` argument allows more.

- **Sources visible.** Numbered references with version and date, not bare links.

- **Honest empty states.** "Not found in indexed sources for React 19" plus nearby topics, never a fabricated answer.

- **Warnings before content.** Stale data, version fallback, deprecation and conflicting sources appear above the answer.

- **CLI is quiet and scriptable.** Progress bars only in a terminal; `--json` everywhere; clear exit codes.

### 7.5 CLI commands (human view)

| Command                       | Purpose                                            |
|-------------------------------|----------------------------------------------------|
| `almanac init`                | Create config, folders and model files             |
| `almanac add <tech>`          | Discover and register sources                      |
| `almanac fetch [tech]`        | Download and clean documentation                   |
| `almanac index`               | Build or update search indexes                     |
| `almanac refresh --due`       | Fetch and index whatever is due; for schedulers    |
| `almanac search "<question>"` | Ask from the terminal, same engine as the MCP tool |
| `almanac serve`               | Run the MCP server                                 |
| `almanac status`              | Technologies, versions, freshness, index size      |
| `almanac test`                | Run the golden question set                        |
| `almanac doctor`              | Check the installation                             |
| `almanac export`              | Copy docs as a folder or archive                   |

## 8. Accuracy, trust and quality strategy

No system can promise perfect accuracy, because source documents can be wrong or unclear. Almanac's strategy is to make wrong answers rare, visible and quick to check.

### 8.1 Accuracy mechanisms

| Mechanism                                          | What it prevents                            | Where specified |
|----------------------------------------------------|---------------------------------------------|-----------------|
| Grounding rule: answers only from retrieved chunks | Invented APIs and memory-based errors       | SRS 8.2         |
| Citation on every claim                            | Unverifiable statements                     | FR-RSP-02       |
| Version pinning from project files                 | Right answer for the wrong version          | FR-VER, SRS 7.4 |
| Nearest-version warning                            | Silent use of another version's docs        | FR-RET-02       |
| Staleness warning                                  | Acting on old pages                         | FR-RSP-06       |
| Source trust ranking                               | Community advice outranking official docs   | SRS 7.6         |
| Conflict display                                   | Hidden disagreement between sources         | FR-RSP-05       |
| Confidence threshold and honest "not found"        | Padding weak matches into confident answers | SRS 7.8         |
| Verbatim code from docs                            | Altered or hallucinated snippets            | FR-RSP-03       |
| Interpretation line                                | Misread questions going unnoticed           | FR-QUN-06       |
| Tests run after use (instruction to Claude)        | Doc snippets that do not fit the project    | SRS 6.4         |

### 8.2 Source trust tiers

| Tier | Source type                                          | Use                                       |
|------|------------------------------------------------------|-------------------------------------------|
| 1    | Official reference and guides, official repo docs    | Primary truth                             |
| 2    | Official blog, RFCs, release notes, maintainer posts | Best practices, rationale, changes        |
| 3    | Vendor or well-known engineering blogs               | Patterns; shown with lower rank and label |
| 4    | Community posts, forums                              | Off by default; opt-in, always labelled   |

### 8.3 Quality loop

1.  Maintain a **golden set** of 30 or more real questions from daily work, each with the expected source page and facts that must appear.

2.  Run it after every change to crawler, chunker, ranking or model; block releases that drop below target.

3.  Review the **weekly report** of weak and empty queries; fix by adding sources, glossary terms or chunking rules.

4.  Spot-check five random answers per week against the live official page.

5.  Track corrections the developer makes to Claude's output that Almanac should have prevented, and add them to the golden set.

### 8.4 Known limits (stated to the user)

- Almanac reports what the documentation says; if the documentation is wrong or out of date, so is the answer, though the as-of date and source help the developer judge.

- Pages blocked by robots rules or behind JavaScript may be missing; `status` lists gaps.

- Best-practice guidance is only as good as the curated sources.

## 9. Assumptions, constraints, dependencies and legal

### 9.1 Assumptions

- The developer uses Claude Code on a Claude Pro account and can add MCP servers.

- The technologies of interest publish public documentation, ideally with `llms.txt`, a sitemap or a public repository.

- The developer's machine can run a small embedding model (about 4 GB RAM available).

- Python is the build language (to be confirmed, see section 12).

- The first set of technologies is chosen by the developer before the build starts.

### 9.2 Constraints

| Constraint                             | Effect on the product                                                        |
|----------------------------------------|------------------------------------------------------------------------------|
| Pro-plan usage limits shared with chat | Responses are compact; no extra model calls inside Almanac                   |
| No paid API dependency                 | Embeddings and re-ranking run locally                                        |
| Single machine                         | No server, no accounts, no multi-user concurrency                            |
| Website politeness                     | Slow, respectful crawling; some sites may be partly unreachable              |
| Sandboxed or restricted networks       | Fetching must be run where outbound access to documentation hosts is allowed |

### 9.3 Dependencies

| Dependency                                             | Why              | Risk if it changes                                      |
|--------------------------------------------------------|------------------|---------------------------------------------------------|
| Claude Code and MCP                                    | Delivery channel | Medium: protocol or config changes need adapter updates |
| Documentation sites and repos                          | Source of truth  | Medium: layout changes break parsers                    |
| SQLite with FTS5                                       | Search index     | Low                                                     |
| Open-source embedding model                            | Semantic search  | Low to medium: model replacement needs re-embedding     |
| Python packages for HTTP, parsing, markdown conversion | Pipeline         | Low: pinned versions                                    |

### 9.4 Legal and licensing

- Respect robots.txt, site terms and rate limits; identify the crawler honestly.

- Prefer official repositories and published `llms.txt` files, which are intended for machine reading.

- Store copies for personal, local use only. Do not redistribute stored documentation or publish the index.

- Keep licence and source URL in each page's front-matter; surface the source in every answer.

- Do not index content behind logins or paywalls without the right to do so.

- Quote sparingly in responses: return the relevant chunk with a link, not whole pages, unless the user asks for a page.

- This section is a product requirement, not legal advice. Check the terms of each source before enabling it.

## 10. Risks and mitigations

The two highest risks are source fragility (sites change or block crawlers) and retrieval quality on casual wording. Both are tested continuously through the golden set and health reports.

| ID  | Risk                                                   | Likelihood | Impact | Mitigation                                                                                                                 | Owner      |
|-----|--------------------------------------------------------|------------|--------|----------------------------------------------------------------------------------------------------------------------------|------------|
| R1  | A documentation site changes layout and parsing breaks | High       | Medium | Prefer llms.txt and git sources; per-source health checks; suspect-page flag; isolate failures                             | Maintainer |
| R2  | Site blocks or throttles the crawler                   | Medium     | Medium | robots.txt compliance, low rate, identifying User-Agent, git fallback                                                      | Maintainer |
| R3  | Casual queries miss the right page                     | Medium     | High   | Hybrid search, query rewriting, glossary, golden set, weekly weak-query review                                             | Developer  |
| R4  | Wrong version's docs used silently                     | Medium     | High   | Lockfile detection, explicit version line, fallback warnings, tests with fixtures                                          | Developer  |
| R5  | Stale data presented as current                        | Medium     | High   | As-of dates, staleness warnings, refresh health report                                                                     | Developer  |
| R6  | Claude does not call Almanac or ignores it             | Medium     | High   | Strong tool descriptions, CLAUDE.md instruction, query-log review of adoption                                              | Developer  |
| R7  | Long responses burn Pro-plan usage                     | Medium     | Medium | Token budget, compact format, section selection                                                                            | Developer  |
| R8  | Source documentation itself is wrong or ambiguous      | Low        | Medium | Trust tiers, conflict display, verify by running tests                                                                     | Developer  |
| R9  | Legal or terms-of-use issues with copying content      | Low        | High   | Local personal use, no redistribution, licence in front-matter, per-source review                                          | Developer  |
| R10 | Embedding model gives poor results on code-heavy text  | Medium     | Medium | Keyword index as equal partner; evaluate two models on golden set; allow swap                                              | Maintainer |
| R11 | MCP or Claude Code behaviour changes                   | Medium     | Medium | Thin adapter layer; pin tested versions; doctor command                                                                    | Maintainer |
| R12 | Index and docs grow too large                          | Low        | Low    | Per-technology page caps; prune old versions; export and archive                                                           | Maintainer |
| R13 | Scope creep before MVP works                           | High       | Medium | Strict release scopes; MVP exit condition                                                                                  | Developer  |
| R14 | Prompt-injection text inside fetched docs              | Low        | High   | Treat docs as data; server never executes content; Claude instructed to treat results as reference only; strip hidden text | Maintainer |

## 11. Roadmap, milestones and release plan

Almanac ships in four gated releases; the MVP comes first because it proves the whole loop end to end before any tuning.

*[Diagram: roadmap, 4 releases and 3 gates, not to scale. See the live document for the drawing.]*

Bands are equal width and not to scale; diamonds mark the gate that must pass before the next release starts. Version 1.0, highlighted, is the release the success metrics apply to.

### 11.1 Release plan

Effort figures are indicative planning estimates for one developer working with Claude Code, to be revised after the MVP.

| Release    | Scope                                                                                                                                                                                            | Indicative effort | Gate to the next release                                                          |
|------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------|-----------------------------------------------------------------------------------|
| MVP (v0.1) | Registry, fetcher, store, keyword index, MCP server with `search_docs`, `get_page`, `list_technologies`, CLI basics, citations; 2 to 3 technologies                                              | 1 to 2 weeks      | A real question asked in Claude Code returns a cited answer from stored docs      |
| v1.0       | Semantic and hybrid search, query understanding, version detection, response bundle, changelog and best-practice layers, honest failure, scheduler and digests, golden-set tests, health reports | 4 to 6 weeks      | Metrics in section 3.3 met on the golden set; refresh runs unattended for 2 weeks |
| v1.1       | Comparison mode, re-ranker, headless rendering, 10 or more technologies                                                                                                                          | 2 to 3 weeks      | 10 or more technologies tracked; golden set still passing                         |
| v2         | Private and internal docs, team sharing                                                                                                                                                          | To be planned     | Decided after v1.1 review                                                         |

### 11.2 Build milestones

| ID  | Milestone                               | Delivers                                                                          | Release |
|-----|-----------------------------------------|-----------------------------------------------------------------------------------|---------|
| M1  | Registry and fetcher                    | Config load; fetch from llms.txt, git and sitemap for two technologies            | MVP     |
| M2  | Cleaner, store, keyword index           | Markdown pages with front-matter; FTS5 search                                     | MVP     |
| M3  | MCP server and CLI                      | Claude Code can call `search_docs`; registration documented                       | MVP     |
| M4  | Embeddings and hybrid search            | Local model; rank fusion; golden-set baseline                                     | v1.0    |
| M5  | Query layer                             | Tech and intent detection, rewriting, glossary, interpretation line               | v1.0    |
| M6  | Version detector                        | Manifest and lockfile parsing; version mapping and fallback warnings              | v1.0    |
| M7  | Response bundle and grounding validator | Sections, citations, token budget, not-found shape                                | v1.0    |
| M8  | Changelog and best-practice layers      | Feeds ingested; deprecation flags; `whats_new`, `best_practices`, `lookup_symbol` | v1.0    |
| M9  | Scheduler and digests                   | `refresh --due`, locks, change digest, setup recipes                              | v1.0    |
| M10 | Quality and hardening                   | Golden set of 30 or more questions, weekly report, doctor, security review        | v1.0    |

### 11.3 What must be decided before the build starts

- The first two or three technologies (open question Q1).

- The build language (open question Q2).

- A first list of trusted best-practice sources for each technology (open question Q3).

## 12. Open questions and glossary

### 12.1 Open questions

| ID  | Question                                                                                               | Needed by        | Default if unanswered                                         |
|-----|--------------------------------------------------------------------------------------------------------|------------------|---------------------------------------------------------------|
| Q1  | Which technologies come first?                                                                         | Before MVP build | Pick 2 to 3 from the developer's current projects             |
| Q2  | Python or Node as the build language?                                                                  | Before MVP build | Python 3.11 or newer                                          |
| Q3  | Which best-practice sources count as trusted for each technology?                                      | v1.0             | Official guides and engineering posts by maintainers only     |
| Q4  | Should community sources (tier 4) ever be enabled?                                                     | v1.0             | Off                                                           |
| Q5  | Where does the refresh job run: laptop timer or a GitHub Action?                                       | v1.0             | Local timer; Action if the laptop is often off                |
| Q6  | Which local embedding model gives the best results on the golden set?                                  | MVP evaluation   | Start with a small general-purpose English model; compare two |
| Q7  | Should the store be a git repository by default?                                                       | MVP              | Yes, local only                                               |
| Q8  | Should Almanac later index private or internal docs?                                                   | v2 planning      | Not in v1                                                     |
| Q9  | Does the developer want the weekly report delivered as a file, a Claude Code prompt or a notification? | v1.0             | File in `reports/`                                            |

### 12.2 Glossary

| Term          | Meaning                                                                  |
|---------------|--------------------------------------------------------------------------|
| Almanac       | The product described in this document                                   |
| MCP           | Model Context Protocol; how Claude Code calls external tools             |
| Bundle        | Structured response to a query                                           |
| Chunk         | Heading-bounded section of a page; unit of search                        |
| Golden set    | Fixed questions with known correct answers for regression testing        |
| Grounding     | Basing every statement on retrieved sources                              |
| Hybrid search | Keyword plus semantic search, merged                                     |
| llms.txt      | A site file that lists LLM-friendly documentation pages                  |
| Trust tier    | Ranking of a source's authority, from 1 (official) to 4 (community)      |
| Staleness     | Time since a page was last fetched                                       |
| SRS           | Software Requirements Specification, the technical companion to this PRD |
