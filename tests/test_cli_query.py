from __future__ import annotations

import json

from conftest import write_config
from typer.testing import CliRunner

from almanac.cli import app

runner = CliRunner()


def inv(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_search_markdown_output(indexed_root):
    res = inv("search", "memo props", "--dir", indexed_root.root, "--tech", "react")
    assert res.exit_code == 0
    assert res.stdout.startswith("**Interpreted as:**")


def test_search_json_is_one_document(indexed_root):
    res = inv("search", "memo props", "--dir", indexed_root.root, "--json", "--tech", "react")
    assert res.exit_code == 0 and json.loads(res.stdout)["status"] == "ok"


def test_search_not_found_exit_8(indexed_root):
    res = inv("search", "quantum flux capacitor configuration", "--dir", indexed_root.root, "--tech", "react")
    assert res.exit_code == 8
    assert res.stdout.strip()


def test_search_index_missing_exit_3(tmp_root):
    write_config(tmp_root, "http://127.0.0.1:1")
    res = inv("search", "memo", "--dir", tmp_root)
    assert res.exit_code == 3
    assert "E_INDEX_MISSING" in res.output


def test_search_other_error_exit_2(indexed_root):
    res = inv("search", "memo", "--dir", indexed_root.root, "--tech", "nope")
    assert res.exit_code == 2
    assert "E_UNKNOWN_TECH" in res.output


def test_search_without_config_exit_2(tmp_root):
    res = inv("search", "memo", "--dir", tmp_root)
    assert res.exit_code == 2 and "almanac init" in res.output


def test_status_json(indexed_root):
    res = inv("status", "--dir", indexed_root.root, "--json")
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    react = next(t for t in data["technologies"] if t["id"] == "react")
    assert react["page_count"] == 5 and "19" in react["versions_indexed"]
    assert {"id", "last_fetch", "last_status", "error_count"} <= set(data["sources"][0])
    assert data["index"]["chunks"] > 0 and data["index"]["db_bytes"] > 0


def test_status_without_index_exits_0(tmp_root):
    write_config(tmp_root, "http://127.0.0.1:1")
    res = inv("status", "--dir", tmp_root, "--json")
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["index"] == {"chunks": 0, "db_bytes": 0}
    assert data["sources"][0]["error_count"] == 0 and data["sources"][0]["last_fetch"] is None
    assert inv("status", "--dir", tmp_root).exit_code == 0


def test_serve_delegates_to_mcp_main(tmp_root, monkeypatch):
    seen = []
    monkeypatch.setattr("almanac.mcp_server.main", seen.append)
    res = inv("serve", "--dir", tmp_root, "--log-level", "WARNING")
    assert res.exit_code == 0 and res.stdout == ""
    assert seen == [["--dir", str(tmp_root), "--log-level", "WARNING"]]
