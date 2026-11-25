import pytest

from almanac.errors import ConfigError
from almanac.registry import load_config


def _tech(extra_src: str = "", tech_id: str = "react", src_id: str = "react-docs", versions: str = '["19"]') -> str:
    return (
        f"  - id: {tech_id}\n"
        "    name: React\n"
        f"    versions: {versions}\n"
        "    sources:\n"
        f"      - id: {src_id}\n"
        "        type: llms_txt\n"
        "        url: https://example.org/llms.txt\n"
        "        kind: reference\n"
        "        trust: 1\n" + extra_src
    )


HEAD = "technologies:\n"

INVALID_CASES = [
    # missing technologies[0].id: the line of the tech item
    (HEAD + "  - name: React\n    sources: []\n", "technologies[0].id", 2),
    (HEAD + _tech().replace("trust: 1", "trust: 5"), "technologies[0].sources[0].trust", 10),
    (HEAD + _tech().replace("llms_txt", "ftp"), "technologies[0].sources[0].type", 7),
    (HEAD + _tech().replace("reference", "tutorial"), "technologies[0].sources[0].kind", 9),
    (
        HEAD
        + _tech()
        .replace("type: llms_txt", "type: url_list")
        .replace("        url: https://example.org/llms.txt\n", ""),
        "technologies[0].sources[0].urls",
        6,
    ),
    (HEAD + _tech().replace("        url: https://example.org/llms.txt\n", ""), "technologies[0].sources[0].url", 6),
    (HEAD + _tech(versions='["nineteen"]'), "technologies[0].versions[0]", 4),
    (
        HEAD + _tech() + _tech(tech_id="vue", src_id="react-docs"),
        "technologies[1].sources[0].id",
        15,
    ),
    (HEAD + _tech(src_id="../x"), "technologies[0].sources[0].id", 6),
    ("setings:\n  contact: a@b.c\n", "setings", 1),
    (HEAD + _tech(tech_id="My React"), "technologies[0].id", 2),
    ("settings:\n  rate_limit_per_host_rps: -1\n", "settings.rate_limit_per_host_rps", 2),
    ("settings:\n  contact: [unclosed\n", "<yaml>", 3),
    (HEAD + _tech(extra_src="        version: nineteen\n"), "technologies[0].sources[0].version", 11),
]


def test_load_valid_config(fixtures):
    cfg = load_config(fixtures / "config/valid.yaml")
    react = cfg.tech("ReactJS")
    assert react.id == "react" and react.default_version() == "19"
    assert [s.type for s in react.sources] == ["llms_txt", "feed", "url_list"]
    s = cfg.settings
    assert (s.response_token_budget, s.rate_limit_per_host_rps, s.stale_after_days) == (3000, 1, 14)
    assert (s.max_pages_per_source, s.max_page_bytes, s.crawl_max_depth, s.max_concurrency) == (2000, 2_000_000, 4, 4)
    assert (s.max_redirects, s.min_content_chars) == (5, 200)
    assert s.allow_private_network is False
    assert cfg.root == (fixtures / "config").resolve()
    assert cfg.tech("python").sources[0].paths == ["Doc"]
    assert cfg.tech("nope") is None


@pytest.mark.parametrize("yaml_text, field, line", INVALID_CASES)
def test_invalid_config_names_file_line_field(tmp_path, yaml_text, field, line):
    p = tmp_path / "almanac.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert e.value.file == p and e.value.field == field and e.value.line == line
    assert str(e.value).startswith(f"{p}:{line}: {field}: ")
