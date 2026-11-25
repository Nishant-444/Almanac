# Almanac

Almanac is a local-first knowledge base of documentation. A Python CLI fetches the docs you choose (an `llms.txt` file, a sitemap, a list of URLs or a git repository) into a folder, and indexes them into a local SQLite full-text search database. A stdio MCP server then lets Claude Code look things up with `search_docs`, `get_page` and `list_technologies`, and every answer cites the stored pages it came from. Nothing is sent anywhere: queries read the local index and never use the network.

## Install

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install .
```

## Setup

Run these in an empty directory that will hold your Almanac data (a "project directory").

```bash
almanac init                                             # creates almanac.yaml, folders
almanac add react --source https://react.dev/llms.txt    # adds a source to a technology
almanac fetch && almanac index                           # first build
claude mcp add almanac -- almanac serve --dir <path>     # <path>: ABSOLUTE path of the project directory
almanac status                                           # shows what is indexed
```

`<path>` in `claude mcp add` must be an absolute path. Claude Code starts the server from its own working directory, so a relative path such as `.` would point somewhere else. For example:

```bash
claude mcp add almanac -- almanac serve --dir /home/me/almanac    # macOS / Linux
claude mcp add almanac -- almanac serve --dir D:\almanac          # Windows
```

After this, Claude Code lists the Almanac tools (check with `/mcp`). A short instruction in the project's `CLAUDE.md` tells Claude when to use them.

You can also query from the shell:

```bash
almanac search "how do I skip re-rendering with memo" --tech react
almanac search "..." --json        # one JSON document on stdout
```

`search` exits 0 on success, 8 when nothing matches, 3 when the index is missing and 2 for bad input or config. Every command accepts `--dir` (or the `ALMANAC_DIR` environment variable) and, where it prints results, `--json`.

## Add this to your project's CLAUDE.md

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

`best_practices`, `explain_error` and `whats_new` are not available in the MVP (see below); the other lines apply.

## MVP limits

- Source types: `llms_txt` (including `llms-full.txt`), `sitemap`, `url_list` and `git`. `feed` and `crawl` sources are accepted in the config, but fetch skips them.
- Retrieval is keyword search (SQLite FTS5) only. There are no embeddings or hybrid search.
- MCP tools are `search_docs`, `get_page` and `list_technologies`. The CLI commands are `init`, `add`, `fetch`, `index`, `search`, `serve` and `status`.
- Not yet implemented: intent classification (the intent is always `["howto"]`), glossary and query rewriting, project version detection (`project_path` is checked but otherwise unused), source discovery in `add` (pass `--source`), `refresh`, `test`, `doctor` and `export`, the full answer sections (best practices, gotchas, example, what's new), marking removed pages, and git auto-commit of the store.
- Fetching is sequential.
- `data_dir` in the config is ignored; data always lives in `./data`.
