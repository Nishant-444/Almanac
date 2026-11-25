import pytest

from almanac.errors import AlmanacError
from almanac.query.understand import build_fts_query, map_version, understand

IDX = {"react": ["18", "19"], "python": ["3.12"]}


def test_map_version():
    assert map_version("18", ["18", "19"]) == ("18", False)
    assert map_version("18.2", ["18", "19"]) == ("18", False)
    assert map_version("18.3", ["18.2", "19"]) == ("18.2", True)
    assert map_version("18.1", ["18.2", "19"]) == ("18.2", True)
    assert map_version("17", ["18", "19"]) == (None, False)
    assert map_version("latest", ["18", "19"]) == ("19", False)
    assert map_version("latest", ["18", "latest"]) == ("latest", False)
    assert map_version("18.x", ["18", "19"]) == ("18", False)


def test_build_fts_query():
    assert build_fts_query("How do I use memo?") == '"memo"'
    assert build_fts_query("React memo props", drop={"react"}) == '"memo" OR "props"'
    assert build_fts_query("the and of") is None
    assert build_fts_query("memo Memo MEMO") == '"memo"'
    assert build_fts_query('"unterminated a:b -x*') == '"unterminated" OR "b" OR "x"'
    assert build_fts_query("café ü") == '"café" OR "ü"'


def test_tech_detection(cfg):
    assert understand("reactjs list re-render", cfg, IDX).tech == "react"
    with pytest.raises(AlmanacError) as e:
        understand("react vs python", cfg, IDX)
    assert e.value.code == "E_AMBIGUOUS" and len(e.value.details["interpretations"]) == 2
    with pytest.raises(AlmanacError) as e:
        understand("list", cfg, IDX)
    assert e.value.code == "E_AMBIGUOUS"
    with pytest.raises(AlmanacError) as e:
        understand("x", cfg, IDX, tech="reakt")
    assert e.value.code == "E_UNKNOWN_TECH" and "react" in e.value.details["closest"]
    with pytest.raises(AlmanacError) as e:
        understand("memo", cfg, {"python": ["3.12"]}, tech="react")
    assert e.value.code == "E_INDEX_MISSING" and "almanac fetch react && almanac index" in e.value.message


def test_single_tech_is_default(cfg):
    one = cfg.model_copy(update={"technologies": [cfg.tech("python")]})
    assert understand("lists", one, IDX).tech == "python"


def test_version_sources(cfg):
    i = understand("memo", cfg, IDX, tech="react", version="18")
    assert (i.version, i.version_source) == ("18", "argument")
    i = understand("React 18 memo", cfg, IDX)
    assert (i.version, i.version_source) == ("18", "query")
    assert understand("react@18.2 memo", cfg, IDX).version == "18"
    i = understand("  react   memo\n props ", cfg, IDX)
    assert (i.version, i.version_source, i.warnings) == ("19", "configured default", [])
    assert i.summary == "React 19 (configured default): react memo props"
    assert i.intent == ["howto"] and i.fts_query == '"memo" OR "props"'


def test_assumed_latest_and_fallback(cfg):
    latest_cfg = cfg.model_copy(
        update={"technologies": [cfg.tech("python").model_copy(update={"versions": ["latest"]})]}
    )
    i = understand("lists", latest_cfg, {"python": ["3.12"]})
    assert i.version_source == "assumed latest" and i.version == "3.12"
    assert i.warnings == [{"code": "VERSION_ASSUMED", "message": "No version evidence; using latest"}]
    i = understand("React 18.3 memo", cfg, {"react": ["18.2", "19"]})
    assert i.version == "18.2"
    assert i.warnings == [{"code": "VERSION_FALLBACK", "message": "Requested 18.3; using docs for 18.2"}]


def test_version_unavailable(cfg):
    with pytest.raises(AlmanacError) as e:
        understand("React 17 memo", cfg, IDX)
    assert e.value.code == "E_VERSION_UNAVAILABLE" and e.value.details["available"] == ["18", "19"]
