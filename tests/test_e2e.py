from __future__ import annotations

import json

from conftest import write_config
from typer.testing import CliRunner

from almanac.cli import app
from almanac.response.bundle import validate_bundle

runner = CliRunner()


def _build(docs_site, root):
    write_config(root, docs_site)
    assert runner.invoke(app, ["fetch", "--dir", str(root)]).exit_code == 0
    assert runner.invoke(app, ["index", "--dir", str(root)]).exit_code == 0


def test_e2e_fetch_index_search(docs_site, tmp_root):  # PRD MVP exit condition, automated half
    _build(docs_site, tmp_root)
    res = runner.invoke(app, ["search", "how do I skip re-rendering with memo", "--dir", str(tmp_root), "--json"])
    b = json.loads(res.stdout)
    assert res.exit_code == 0 and b["status"] == "ok"
    assert b["sources"][0]["url"].startswith(docs_site) and validate_bundle(b) == []


def test_e2e_not_found_exit_8(docs_site, tmp_root):
    _build(docs_site, tmp_root)
    res = runner.invoke(app, ["search", "quantum flux capacitor configuration", "--dir", str(tmp_root)])
    assert res.exit_code == 8


def test_e2e_unicode_page(docs_site, tmp_root):
    _build(docs_site, tmp_root)
    res = runner.invoke(app, ["search", "Pfeile", "--dir", str(tmp_root), "--json"])
    b = json.loads(res.stdout)
    assert res.exit_code == 0 and "日本" in b["answer"]["text"]


def test_e2e_status_reports_fetch_log(docs_site, tmp_root):
    _build(docs_site, tmp_root)
    data = json.loads(runner.invoke(app, ["status", "--dir", str(tmp_root), "--json"]).stdout)
    src = data["sources"][0]
    assert src["id"] == "react-docs" and src["last_fetch"] and src["error_count"] >= 1  # robots + missing
    assert data["technologies"][0]["page_count"] == 3


def test_status_separates_sources_on_one_host(docs_site, tmp_root):
    good = {"id": "good", "type": "url_list", "urls": [f"{docs_site}/reference/memo.html"], "kind": "guide", "trust": 2}
    bad = {"id": "bad", "type": "url_list", "urls": [f"{docs_site}/nope.html"], "kind": "guide", "trust": 2}
    write_config(tmp_root, docs_site, extra_sources=[good, bad])
    runner.invoke(app, ["fetch", "--dir", str(tmp_root)])
    data = json.loads(runner.invoke(app, ["status", "--dir", str(tmp_root), "--json"]).stdout)
    by_id = {s["id"]: s for s in data["sources"]}
    assert by_id["good"]["error_count"] == 0 and by_id["bad"]["error_count"] >= 1
    assert by_id["good"]["last_status"] != by_id["bad"]["last_status"]


def test_piped_json_is_utf8(docs_site, tmp_root):
    """F3: a real subprocess with stdout piped (cp1252 by default on Windows) still emits UTF-8 JSON."""
    import os
    import subprocess
    import sys

    _build(docs_site, tmp_root)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    proc = subprocess.run(
        [sys.executable, "-m", "almanac", "search", "Pfeile", "--dir", str(tmp_root), "--json"],
        check=False,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    b = json.loads(proc.stdout.decode("utf-8"))
    assert "Pfeile → ü 日本" in b["answer"]["text"]
