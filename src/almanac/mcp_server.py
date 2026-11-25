"""stdio MCP server. stdout belongs to the protocol: nothing here may print to it."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from almanac import logs
from almanac.engine import Engine, error_response
from almanac.errors import AlmanacError, ConfigError
from almanac.paths import AlmanacPaths

_SEARCH_DESC = (
    "Search current, version-correct official documentation tracked by Almanac. Call this BEFORE writing or "
    "changing code that uses a library, framework or tool, and whenever you are unsure an API exists or how it "
    "behaves. Pass a plain-English description of what you need; pass project_path when known. Returns a cited "
    "answer with sources; if status is not_found, say so instead of guessing."
)
_PAGE_DESC = (
    "Read the full cleaned documentation page (or one section by anchor) for a search_docs hit when the excerpt "
    "is not enough."
)
_LIST_DESC = (
    "List technologies and versions Almanac has indexed, with page counts and freshness. Call to check coverage "
    "before relying on Almanac for a technology."
)


def _dump(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def create_server(root: Path) -> MCPServer:
    engine = Engine(root)
    server = MCPServer("almanac")

    @server.tool(name="search_docs", description=_SEARCH_DESC, structured_output=False)
    def search_docs(
        query: str,
        tech: str | None = None,
        version: str | None = None,
        project_path: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if not 3 <= len(query) <= 2000:
            return _dump(error_response(AlmanacError("E_BAD_INPUT", "query must be 3 to 2000 characters long")))
        if max_tokens is not None and not 500 <= max_tokens <= 12000:
            return _dump(error_response(AlmanacError("E_BAD_INPUT", "max_tokens must be between 500 and 12000")))
        return _dump(engine.search_docs(query, tech, version, project_path, max_tokens))

    @server.tool(name="get_page", description=_PAGE_DESC, structured_output=False)
    def get_page(
        id: str | None = None,
        tech: str | None = None,
        path: str | None = None,
        version: str | None = None,
        section: str | None = None,
    ) -> str:
        return _dump(engine.get_page(id=id, tech=tech, path=path, version=version, section=section))

    @server.tool(name="list_technologies", description=_LIST_DESC, structured_output=False)
    def list_technologies() -> str:
        return _dump(engine.list_technologies())

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="almanac-mcp", description="Almanac stdio MCP server")
    parser.add_argument("--dir", default=".", help="Almanac project directory (contains almanac.yaml)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    args = parser.parse_args(argv)

    root = Path(args.dir)
    paths = AlmanacPaths(root)
    logs.configure(paths, uuid.uuid4().hex[:12], args.log_level)
    logging.captureWarnings(True)
    try:
        server = create_server(root)
    except ConfigError as e:
        hint = "run `almanac init` to create one" if not paths.config.exists() else "fix almanac.yaml and retry"
        print(f"almanac: {e}\nhint: {hint}", file=sys.stderr)
        sys.exit(2)
    logging.getLogger("almanac.mcp_server").info("server started")
    server.run("stdio")


if __name__ == "__main__":
    main()
