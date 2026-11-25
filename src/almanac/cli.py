from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import typer
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from almanac import logs, mcp_server
from almanac.db import connect
from almanac.engine import Engine
from almanac.errors import ConfigError
from almanac.models import Kind, SourceType
from almanac.paths import AlmanacPaths
from almanac.pipeline import SelectionError, run_fetch, run_index
from almanac.registry import load_config
from almanac.response.bundle import render_markdown

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Almanac: a local-first docs knowledge base.")

DirOpt = Annotated[
    Path, typer.Option("--dir", envvar="ALMANAC_DIR", help="Project directory (default: $ALMANAC_DIR or cwd).")
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print one JSON document to stdout.")]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_TEMPLATE = """\
# Almanac configuration. See `almanac add --help` to add sources.
settings:
  # data_dir is ignored in the MVP: data always lives in ./data
  data_dir: ./data
  # contact: you@example.org   # sent in the User-Agent
  embedding_model: {model}
  allow_private_network: false
  response_token_budget: 3000
  default_refresh_hours: 24
  rate_limit_per_host_rps: 1
  stale_after_days: 14
  max_concurrency: 4
  crawl_max_depth: 4
  max_pages_per_source: 2000
  max_page_bytes: 2000000
  max_redirects: 5
  min_content_chars: 200

technologies: []
# Example:
# technologies:
#   - id: react
#     name: React
#     aliases: [reactjs]
#     versions: ["19", "18"]
#     sources:
#       - {{id: react-docs, type: llms_txt, url: "https://react.dev/llms.txt", kind: reference, trust: 1}}
"""


def _utf8_stdio() -> None:
    """Write UTF-8 to stdout/stderr even when piped on Windows (default cp1252 mangles text and --json)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


@app.callback()
def _main(ctx: typer.Context) -> None:
    # serve is excluded: the MCP SDK owns stdout there.
    if ctx.invoked_subcommand != "serve":
        _utf8_stdio()


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _emit(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False))


def _load(root: Path):
    paths = AlmanacPaths(root)
    if not paths.config.exists():
        raise _fail("almanac.yaml not found; run: almanac init", 2)
    try:
        return load_config(paths.config)
    except ConfigError as e:
        raise _fail(f"{e}\nFix the field above in almanac.yaml and rerun.", 2) from e


@app.command()
def init(
    dir_: DirOpt = Path("."),
    json_: JsonOpt = False,
    model: Annotated[str, typer.Option("--model", help="Embedding model name.")] = "bge-small-en",
) -> None:
    """Create almanac.yaml, glossary.yaml and the folder layout."""
    paths = AlmanacPaths(dir_)
    created: list[str] = []
    try:
        paths.root.mkdir(parents=True, exist_ok=True)
        existed = paths.config.exists()
        if not existed:
            paths.config.write_text(_TEMPLATE.format(model=model), encoding="utf-8", newline="\n")
            created.append("almanac.yaml")
        if not paths.glossary.exists():
            paths.glossary.write_text("terms: {}\n", encoding="utf-8", newline="\n")
            created.append("glossary.yaml")
        for d in (paths.docs, paths.data, paths.golden, paths.reports, paths.logs):
            d.mkdir(exist_ok=True)
        if shutil.which("git") and not (paths.root / ".git").exists():
            subprocess.run(["git", "init", "-q", str(paths.root)], check=False, capture_output=True)
            gi = paths.root / ".gitignore"
            if not gi.exists():
                gi.write_text("data/\nlogs/\n", encoding="utf-8", newline="\n")
    except OSError as e:
        raise _fail(f"cannot write to {paths.root}: {e.strerror or e}", 3) from e
    message = "already initialised" if existed else f"initialised {paths.root}"
    if json_:
        _emit({"root": str(paths.root), "created": created, "message": message})
    else:
        typer.echo(message)


def _infer_type(url: str) -> str:
    low = url.lower().split("?")[0].rstrip("/")
    name = low.rsplit("/", 1)[-1]
    if name.endswith(("llms.txt", "llms-full.txt")):
        return "llms_txt"
    if "sitemap" in name and name.endswith(".xml"):
        return "sitemap"
    if low.endswith(".git") or re.match(r"^https?://github\.com/[^/]+/[^/]+$", low):
        return "git"
    return "url_list"


def _source_urls(src: Any) -> list[str]:
    return [*([src["url"]] if src.get("url") else []), *(src.get("urls") or [])]


@app.command()
def add(
    tech: Annotated[str, typer.Argument(help="Technology id, e.g. react.")],
    dir_: DirOpt = Path("."),
    json_: JsonOpt = False,
    source: Annotated[str | None, typer.Option("--source", help="Source URL.")] = None,
    type_: Annotated[str | None, typer.Option("--type", help="Source type (inferred from the URL if omitted).")] = None,
    kind: Annotated[str, typer.Option("--kind")] = "reference",
    trust: Annotated[int, typer.Option("--trust")] = 1,
    name: Annotated[str | None, typer.Option("--name", help="Display name for a new technology.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Accept defaults without prompting.")] = False,
) -> None:
    """Add a source to a technology (creating the technology if needed)."""
    if source is None:
        raise _fail("Source discovery arrives in v1.0; pass --source URL", 4)
    _load(dir_)
    paths = AlmanacPaths(dir_)
    if not _ID_RE.match(tech):
        raise _fail(f"invalid technology id '{tech}': use lowercase letters, digits, '-' and '_'", 2)
    type_ = type_ or _infer_type(source)
    if type_ not in {t.value for t in SourceType}:
        raise _fail(f"invalid --type '{type_}'", 2)
    if kind not in {k.value for k in Kind}:
        raise _fail(f"invalid --kind '{kind}'", 2)

    original = paths.config.read_text(encoding="utf-8")
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(original)
    techs = data.get("technologies")
    if techs is None:
        techs = data["technologies"] = CommentedSeq()
    techs.fa.set_block_style()
    entry = next((t for t in techs if t.get("id") == tech), None)
    if entry is not None and any(source in _source_urls(s) for s in entry.get("sources") or []):
        raise _fail(f"{source} is already a source of '{tech}'", 2)
    if entry is None:
        entry = CommentedMap(id=tech, name=name or tech.replace("-", " ").replace("_", " ").title())
        entry["sources"] = CommentedSeq()
        techs.append(entry)
    sources = entry["sources"]
    sources.fa.set_block_style()
    taken = {s.get("id") for t in techs for s in t.get("sources") or []}
    source_id, n = f"{tech}-{type_}", 2
    while source_id in taken:
        source_id, n = f"{tech}-{type_}-{n}", n + 1
    new = CommentedMap(id=source_id, type=type_)
    if type_ == "url_list":
        new["urls"] = [source]
    else:
        new["url"] = source
    new["kind"] = kind
    new["trust"] = trust
    sources.append(new)

    tmp = paths.config.with_name(paths.config.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            yaml.dump(data, fh)
        load_config(tmp)
        os.replace(tmp, paths.config)
    except ConfigError as e:
        tmp.unlink(missing_ok=True)
        raise _fail(f"{str(e).replace('.yaml.tmp', '.yaml')}\nNothing was changed.", 2) from e
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if json_:
        _emit({"tech": tech, "source_id": source_id, "type": type_, "url": source})
    else:
        typer.echo(f"added {source_id} ({type_}) to {tech}")


def _run_setup(dir_: Path) -> AlmanacPaths:
    _load(dir_)
    paths = AlmanacPaths(dir_)
    try:
        logs.configure(paths, uuid4().hex[:12])
    except OSError as e:
        raise _fail(f"cannot write logs: {e.strerror or e}", 3) from e
    return paths


@app.command()
def fetch(
    tech: Annotated[str | None, typer.Argument(help="Only this technology.")] = None,
    dir_: DirOpt = Path("."),
    json_: JsonOpt = False,
    source: Annotated[str | None, typer.Option("--source", help="Only this source id.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Ignore cached validators.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List sources without network access.")] = False,
) -> None:
    """Fetch configured sources into docs/."""
    _run_setup(dir_)
    try:
        rep = run_fetch(dir_, tech=tech, source=source, force=force, dry_run=dry_run)
    except SelectionError as e:
        raise _fail(str(e), 2) from e
    except ConfigError as e:
        raise _fail(f"{e}\nFix the field above in almanac.yaml and rerun.", 2) from e
    except (OSError, sqlite3.Error) as e:
        raise _fail(f"fetch failed: {e}", 3) from e
    if json_:
        _emit(
            {
                "ok": rep.ok,
                "failed": rep.failed,
                "skipped": rep.skipped,
                "changes": {k.value: v for k, v in rep.changes.items()},
                "dry_run": dry_run,
            }
        )
    else:
        verb = "would fetch" if dry_run else "ok"
        for sid in rep.ok:
            typer.echo(f"{verb}: {sid}")
        for sid, reason in rep.failed.items():
            typer.echo(f"FAILED: {sid}: {reason}")
        for sid, reason in rep.skipped.items():
            typer.echo(f"skipped: {sid}: {reason}")
        if not dry_run:
            c = rep.changes
            typer.echo(f"pages: {c['new']} new, {c['changed']} changed, {c['unchanged']} unchanged")
    if rep.failed:
        raise typer.Exit(6 if not rep.ok else 5)


@app.command()
def index(
    dir_: DirOpt = Path("."),
    json_: JsonOpt = False,
    rebuild: Annotated[bool, typer.Option("--rebuild", help="Re-index every page.")] = False,
    tech: Annotated[str | None, typer.Option("--tech", help="Only this technology.")] = None,
) -> None:
    """Index stored pages into the search database."""
    _run_setup(dir_)
    try:
        stats = run_index(dir_, tech=tech, rebuild=rebuild)
    except SelectionError as e:
        raise _fail(str(e), 2) from e
    except ConfigError as e:
        raise _fail(f"{e}\nFix the field above in almanac.yaml and rerun.", 2) from e
    except (OSError, sqlite3.Error) as e:
        raise _fail(f"index failed: {e}", 3) from e
    if json_:
        _emit(asdict(stats))
    else:
        typer.echo(
            f"indexed {stats.pages_indexed} pages ({stats.chunks_written} chunks), "
            f"skipped {stats.pages_skipped}, deleted {stats.pages_deleted}"
        )


def _engine(root: Path) -> Engine:
    _load(root)
    try:
        return Engine(root)
    except ConfigError as e:
        raise _fail(f"{e}\nFix the field above in almanac.yaml and rerun.", 2) from e


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Plain-English question.")],
    dir_: DirOpt = Path("."),
    json_: JsonOpt = False,
    tech: Annotated[str | None, typer.Option("--tech", help="Restrict to a technology id.")] = None,
    version: Annotated[str | None, typer.Option("--version", help="Restrict to a version.")] = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", help="Response token budget.")] = None,
) -> None:
    """Search the indexed docs and print a cited answer."""
    result = _engine(dir_).search_docs(query, tech=tech, version=version, max_tokens=max_tokens)
    status = result.get("status")
    if json_:
        _emit(result)
    elif status == "ok":
        typer.echo(render_markdown(result), nl=False)
    elif status == "not_found":
        typer.echo(result.get("message", "No results."))
        nearby = result.get("nearby_topics") or []
        if nearby:
            typer.echo("Nearby topics: " + ", ".join(str(n) for n in nearby))
    else:
        typer.echo(f"{result.get('code', 'E_INTERNAL')}: {result.get('message', '')}", err=True)
    if status == "ok":
        return
    if status == "not_found":
        raise typer.Exit(8)
    raise typer.Exit(3 if result.get("code") == "E_INDEX_MISSING" else 2)


@app.command()
def serve(
    dir_: DirOpt = Path("."),
    log_level: Annotated[str, typer.Option("--log-level", help="DEBUG, INFO, WARNING, ERROR or CRITICAL.")] = "INFO",
) -> None:
    """Run the MCP server on stdio (stdout carries the protocol)."""
    mcp_server.main(["--dir", str(dir_), "--log-level", log_level])


def _source_stats(conn: sqlite3.Connection | None, source_id: str) -> dict[str, Any]:
    stats: dict[str, Any] = {"last_fetch": None, "last_status": None, "error_count": 0}
    if conn is None:
        return stats
    rows = conn.execute(
        "SELECT status, error, at FROM fetch_log WHERE source_id = ? ORDER BY at, id", (source_id,)
    ).fetchall()
    if rows:
        last = rows[-1]
        stats["last_fetch"] = max((r["at"] for r in rows if r["at"]), default=None)
        stats["last_status"] = last["error"] if last["error"] is not None else last["status"]
        stats["error_count"] = sum(1 for r in rows if r["error"] is not None)
    return stats


@app.command()
def status(dir_: DirOpt = Path("."), json_: JsonOpt = False) -> None:
    """Show indexed technologies, fetch health and index size."""
    engine = _engine(dir_)
    techs = engine.list_technologies().get("technologies", [])
    paths = engine.paths
    conn = connect(paths.db, readonly=True) if paths.db.exists() else None
    try:
        sources = []
        for t in engine.config.technologies:
            for s in t.sources:
                stats = _source_stats(conn, s.id)
                sources.append({"id": s.id, **stats})
        chunks = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0] if conn is not None else 0
    except sqlite3.Error as e:
        raise _fail(f"cannot read the index: {e}", 3) from e
    finally:
        if conn is not None:
            conn.close()
    data = {
        "technologies": [
            {"id": t["id"], "versions_indexed": t["versions_indexed"], "page_count": t["page_count"]} for t in techs
        ],
        "sources": sources,
        "index": {"chunks": chunks, "db_bytes": paths.db.stat().st_size if paths.db.exists() else 0},
    }
    if json_:
        _emit(data)
        return
    typer.echo(f"{'technology':<20}{'versions':<20}pages")
    for t in data["technologies"]:
        typer.echo(f"{t['id']:<20}{', '.join(t['versions_indexed']) or '-':<20}{t['page_count']}")
    typer.echo("")
    typer.echo(f"{'source':<24}{'last fetch':<28}{'last status':<14}errors")
    for s in sources:
        typer.echo(
            f"{s['id']:<24}{s['last_fetch'] or '-':<28}{s['last_status'] if s['last_status'] is not None else '-':<14}{s['error_count']}"
        )
    typer.echo("")
    typer.echo(f"index: {chunks} chunks, {data['index']['db_bytes']} bytes")
