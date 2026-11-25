from __future__ import annotations

import json
import socket

from conftest import write_config
from typer.testing import CliRunner

from almanac.cli import app
from almanac.registry import load_config

runner = CliRunner()


DEAD = {"id": "dead", "type": "url_list", "urls": ["http://127.0.0.1:9/x"], "kind": "guide", "trust": 1}


def inv(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_init_creates_layout(tmp_path):
    res = inv("init", "--dir", tmp_path)
    assert res.exit_code == 0
    for p in ["almanac.yaml", "glossary.yaml", "docs", "data", "golden", "reports", "logs"]:
        assert (tmp_path / p).exists()
    assert load_config(tmp_path / "almanac.yaml").technologies == []
    again = inv("init", "--dir", tmp_path)
    assert again.exit_code == 0 and "already initialised" in again.output


def test_add_infers_type_and_keeps_comments(tmp_path):
    inv("init", "--dir", tmp_path)
    assert inv("add", "react", "--source", "https://react.dev/llms.txt", "--dir", tmp_path).exit_code == 0
    assert inv("add", "react", "--source", "https://react.dev/sitemap.xml", "--dir", tmp_path).exit_code == 0
    assert inv("add", "react", "--source", "https://github.com/facebook/react", "--dir", tmp_path).exit_code == 0
    assert inv("add", "react", "--source", "https://react.dev/blog", "--dir", tmp_path).exit_code == 0
    text = (tmp_path / "almanac.yaml").read_text(encoding="utf-8")
    assert "# Almanac configuration" in text and "#   - id: react" in text
    srcs = load_config(tmp_path / "almanac.yaml").technologies[0].sources
    assert [(s.id, s.type.value) for s in srcs] == [
        ("react-llms_txt", "llms_txt"),
        ("react-sitemap", "sitemap"),
        ("react-git", "git"),
        ("react-url_list", "url_list"),
    ]
    assert load_config(tmp_path / "almanac.yaml").technologies[0].name == "React"


def test_add_id_suffix(tmp_path):
    inv("init", "--dir", tmp_path)
    inv("add", "react", "--source", "https://a.dev/llms.txt", "--dir", tmp_path)
    inv("add", "react", "--source", "https://b.dev/llms.txt", "--dir", tmp_path)
    ids = [s.id for s in load_config(tmp_path / "almanac.yaml").technologies[0].sources]
    assert ids == ["react-llms_txt", "react-llms_txt-2"]


def test_add_without_source_exit_4(tmp_path):
    inv("init", "--dir", tmp_path)
    res = inv("add", "react", "--dir", tmp_path)
    assert res.exit_code == 4 and "Source discovery arrives in v1.0; pass --source URL" in res.output


def test_add_duplicate_exit_2(tmp_path):
    inv("init", "--dir", tmp_path)
    assert inv("add", "react", "--source", "https://react.dev/llms.txt", "--dir", tmp_path).exit_code == 0
    before = (tmp_path / "almanac.yaml").read_text(encoding="utf-8")
    res = inv("add", "react", "--source", "https://react.dev/llms.txt", "--dir", tmp_path)
    assert res.exit_code == 2 and "already" in res.output
    assert (tmp_path / "almanac.yaml").read_text(encoding="utf-8") == before


def test_add_bad_input_exit_2(tmp_path):
    inv("init", "--dir", tmp_path)
    assert inv("add", "react", "--source", "https://x.dev/a", "--trust", 9, "--dir", tmp_path).exit_code == 2
    assert inv("add", "React!", "--source", "https://x.dev/a", "--dir", tmp_path).exit_code == 2
    assert load_config(tmp_path / "almanac.yaml").technologies == []


def test_missing_config_exit_2(tmp_path):
    res = inv("fetch", "--dir", tmp_path)
    assert res.exit_code == 2 and "almanac init" in res.output


def test_invalid_config_exit_2_with_location(tmp_root):
    write_config(tmp_root, "http://x")
    text = (tmp_root / "almanac.yaml").read_text(encoding="utf-8").replace('"trust": 1', '"trust": 9')
    (tmp_root / "almanac.yaml").write_text(text, encoding="utf-8")
    res = inv("fetch", "--dir", tmp_root)
    assert res.exit_code == 2
    assert "almanac.yaml:" in res.output and "trust" in res.output and "Fix the field above" in res.output


def test_fetch_exit_5_on_partial_failure(docs_site, tmp_root):
    write_config(tmp_root, docs_site, extra_sources=[DEAD])
    assert inv("fetch", "--dir", tmp_root).exit_code == 5


def test_fetch_total_failure_exit_6(tmp_root):
    write_config(tmp_root, "http://127.0.0.1:9")
    assert inv("fetch", "--dir", tmp_root).exit_code == 6


def test_fetch_then_index_json(docs_site, tmp_root):
    write_config(tmp_root, docs_site)
    res = inv("fetch", "--json", "--dir", tmp_root)
    assert res.exit_code == 0
    assert json.loads(res.stdout)["ok"] == ["react-docs"]
    first = inv("index", "--json", "--dir", tmp_root)
    assert first.exit_code == 0 and json.loads(first.stdout)["pages_indexed"] >= 2
    second = inv("index", "--json", "--dir", tmp_root)
    assert json.loads(second.stdout)["pages_indexed"] == 0


def test_fetch_dry_run_no_network(tmp_root, monkeypatch):
    write_config(tmp_root, "http://127.0.0.1:9")

    def refuse(*a, **kw):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    res = inv("fetch", "--dry-run", "--dir", tmp_root)
    assert res.exit_code == 0 and "react-docs" in res.output


def test_fetch_unknown_source_exit_2(tmp_root):
    write_config(tmp_root, "http://127.0.0.1:9")
    assert inv("fetch", "--source", "nope", "--dir", tmp_root).exit_code == 2


def test_add_dump_failure_leaves_original(tmp_path, monkeypatch):
    from ruamel.yaml import YAML

    inv("init", "--dir", tmp_path)
    cfg = tmp_path / "almanac.yaml"
    before = cfg.read_bytes()

    def boom(self, data, stream=None, **kw):
        stream.write("technologies: [")
        raise OSError("disk full")

    monkeypatch.setattr(YAML, "dump", boom)
    res = inv("add", "react", "--source", "https://react.dev/llms.txt", "--dir", tmp_path)
    assert res.exit_code != 0
    assert cfg.read_bytes() == before
    assert not (tmp_path / "almanac.yaml.tmp").exists()


def test_add_validation_failure_leaves_original(tmp_path):
    inv("init", "--dir", tmp_path)
    cfg = tmp_path / "almanac.yaml"
    before = cfg.read_bytes()
    res = inv("add", "react", "--source", "https://x.dev/a", "--trust", 9, "--dir", tmp_path)
    assert res.exit_code == 2
    assert cfg.read_bytes() == before
    assert not (tmp_path / "almanac.yaml.tmp").exists()


def test_fetch_disabled_source_exit_2(tmp_root):
    off = {**DEAD, "id": "off", "enabled": False}
    write_config(tmp_root, "http://127.0.0.1:9", extra_sources=[off])
    assert inv("fetch", "--source", "off", "--dry-run", "--dir", tmp_root).exit_code == 2


def test_index_skips_broken_and_frontmatterless_md(docs_site, tmp_root):
    """M7: a truncated page and a README under docs/ never crash index; good pages still index."""
    write_config(tmp_root, docs_site)
    assert runner.invoke(app, ["fetch", "--dir", str(tmp_root)]).exit_code == 0
    docs = tmp_root / "docs"
    (docs / "README.md").write_text("# About these docs\n", encoding="utf-8", newline="\n")
    (docs / "react" / "19" / "broken.md").write_text("---\nid: react/19/broken\ntitle: [unclosed\n", encoding="utf-8")
    (docs / "react" / "19" / "partial.md").write_text("---\nid: react/19/partial\n---\nbody\n", encoding="utf-8")
    res = runner.invoke(app, ["index", "--dir", str(tmp_root), "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["pages_indexed"] == 3
    log = (tmp_root / "logs" / "almanac.jsonl").read_text(encoding="utf-8")
    assert "broken.md" in log and "partial.md" in log


def test_index_unknown_tech_exit_2(docs_site, tmp_root):
    """Q16: --tech is resolved through the config; unknown or path-like values are bad args."""
    write_config(tmp_root, docs_site)
    for bad in ("nope", "../x"):
        res = runner.invoke(app, ["index", "--dir", str(tmp_root), "--tech", bad])
        assert res.exit_code == 2, res.output
