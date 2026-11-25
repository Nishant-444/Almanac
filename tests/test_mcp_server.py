import asyncio
import json
import logging
import os
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from almanac import logs
from almanac.mcp_server import create_server


def _payload(res):
    return json.loads(res.content[0].text)


async def test_tools_listed_with_schemas(indexed_root):
    srv = create_server(indexed_root.root)
    tools = {t.name: t for t in await srv.list_tools()}
    assert set(tools) == {"search_docs", "get_page", "list_technologies"}
    assert tools["search_docs"].input_schema["required"] == ["query"]
    assert "BEFORE writing or changing code" in tools["search_docs"].description
    assert tools["get_page"].description.startswith("Read the full cleaned documentation page")
    assert tools["list_technologies"].description.startswith("List technologies and versions")


async def test_tools_call_engine(indexed_root):
    srv = create_server(indexed_root.root)
    res = await srv.call_tool("search_docs", {"query": "memo props", "tech": "react"})
    assert _payload(res)["status"] == "ok"
    res = await srv.call_tool("list_technologies", {})
    assert {t["id"] for t in _payload(res)["technologies"]} >= {"react", "python"}


async def test_bad_input(indexed_root):
    srv = create_server(indexed_root.root)
    for args in (
        {"query": "hi"},
        {"query": "memo props", "max_tokens": 100},
        {"query": "memo props", "max_tokens": 99999},
        {"query": "x" * 2001},
    ):
        body = _payload(await srv.call_tool("search_docs", args))
        assert body["status"] == "error"
        assert body["code"] == "E_BAD_INPUT"
    assert "query" in _payload(await srv.call_tool("search_docs", {"query": "hi"}))["message"]
    assert (
        "max_tokens"
        in _payload(await srv.call_tool("search_docs", {"query": "memo props", "max_tokens": 100}))["message"]
    )


def test_logs_configure_is_idempotent_and_never_stdout(indexed_root):
    paths = indexed_root.paths
    log = logs.configure(paths, "run1", stderr=True)
    logs.configure(paths, "run1", stderr=True)
    try:
        assert paths.logs.is_dir()
        assert not log.propagate
        assert len(log.handlers) == 2
        for h in log.handlers:
            assert getattr(h, "stream", None) is not sys.stdout
        logging.getLogger("almanac.test").info("hello")
        line = json.loads(paths.log_file.read_text(encoding="utf-8").splitlines()[-1])
        assert line["msg"] == "hello" and line["run_id"] == "run1" and line["level"] == "INFO"
        assert {"ts", "module"} <= set(line)
    finally:
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)


def test_main_invalid_config_exits_2_on_stderr(tmp_path):
    (tmp_path / "almanac.yaml").write_text("sources: [unclosed\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "almanac.mcp_server", "--dir", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "almanac.yaml" in proc.stderr


def test_main_missing_config_exits_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "almanac.mcp_server", "--dir", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "almanac init" in proc.stderr


@pytest.mark.parametrize(
    "entry",
    [["-m", "almanac.mcp_server"], ["-m", "almanac", "serve"]],  # the latter is what the README registers
    ids=["mcp_server", "cli_serve"],
)
async def test_stdio_session_end_to_end(indexed_root, entry):
    # Unbuffered, so a stray print reaches the pipe at once instead of at exit (after the session closed).
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    params = StdioServerParameters(command=sys.executable, args=[*entry, "--dir", str(indexed_root.root)], env=env)
    faults: list[Exception] = []

    async def on_message(message) -> None:
        # The SDK surfaces unparseable stdout lines (anything but JSON-RPC) here as exceptions.
        if isinstance(message, Exception):
            faults.append(message)

    async def session():
        async with stdio_client(params) as (r, w), ClientSession(r, w, message_handler=on_message) as s:
            await s.initialize()
            tools = await s.list_tools()
            assert {t.name for t in tools.tools} == {"search_docs", "get_page", "list_technologies"}
            res = await s.call_tool("search_docs", {"query": "memo props", "tech": "react"})
            assert _payload(res)["status"] == "ok"
            res = await s.call_tool("search_docs", {"query": "hi"})
            assert _payload(res)["code"] == "E_BAD_INPUT"
            await asyncio.sleep(0.2)  # let any fault delivery task run before the session closes

    await asyncio.wait_for(session(), 20)
    assert faults == [], f"non-JSON-RPC output on the server's stdout: {faults!r}"
    lines = [json.loads(x) for x in indexed_root.paths.log_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines and all({"run_id", "level"} <= set(x) for x in lines)


def test_serve_stdout_is_only_jsonrpc(indexed_root):
    """Every byte the registered entry point writes to stdout, through exit, is a JSON-RPC 2.0 line."""
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_docs", "arguments": {"query": "memo props", "tech": "react"}},
        },
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", "almanac", "serve", "--dir", str(indexed_root.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out = b""
        for req in requests:
            proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
            proc.stdin.flush()
            if "id" in req:  # wait for this response before sending the next request
                line = proc.stdout.readline()
                out += line
                assert json.loads(line)["id"] == req["id"]
        proc.stdin.close()
        out += proc.stdout.read()  # everything else, up to and including process exit
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()
    lines = [x for x in out.decode("utf-8").splitlines() if x.strip()]
    assert len(lines) >= 3
    for line in lines:
        msg = json.loads(line)
        assert msg["jsonrpc"] == "2.0", line
    assert json.loads(json.loads(lines[-1])["result"]["content"][0]["text"])["status"] == "ok"


async def test_max_tokens_defaults_to_settings_budget(indexed_root, monkeypatch):
    """Q11: without max_tokens the engine applies settings.response_token_budget (not a hard-coded 3000)."""
    from almanac.engine import Engine

    seen = []
    real = Engine.search_docs

    def spy(self, *args, **kwargs):
        seen.append(args[4] if len(args) > 4 else kwargs.get("max_tokens"))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Engine, "search_docs", spy)
    srv = create_server(indexed_root.root)
    tools = {t.name: t for t in await srv.list_tools()}
    assert tools["search_docs"].input_schema["properties"]["max_tokens"].get("default") is None
    assert _payload(await srv.call_tool("search_docs", {"query": "memo props", "tech": "react"}))["status"] == "ok"
    assert (
        _payload(await srv.call_tool("search_docs", {"query": "memo props", "tech": "react", "max_tokens": 800}))[
            "status"
        ]
        == "ok"
    )
    assert seen == [None, 800]
