from collections import namedtuple
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_almanac_logger():
    """CLI runs attach a file handler (and propagate=False) to the global logger; undo it after every test."""
    import logging

    log = logging.getLogger("almanac")
    before, propagate, level = list(log.handlers), log.propagate, log.level
    yield
    log.propagate, log.level = propagate, level
    for h in list(log.handlers):
        if h not in before:
            log.removeHandler(h)
            h.close()


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def make_page():
    from datetime import UTC, datetime

    from almanac.models import Kind, Page

    def factory(**overrides) -> Page:
        values = {
            "id": "react/19/memo",
            "tech": "react",
            "version": "19",
            "kind": Kind.reference,
            "trust": 1,
            "title": "useMemo",
            "source_url": "https://example.org/reference/react/useMemo",
            "source_id": "react-docs",
            "fetched_at": datetime(2026, 11, 24, 6, 10, tzinfo=UTC),
            "content_hash": "sha256:stale",
            "body": "# useMemo\n\nText\n",
        }
        values.update(overrides)
        return Page(**values)

    return factory


@pytest.fixture
def docs_site(fixtures):
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    root = fixtures / "site"
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_ALMANAC_YAML = """\
technologies:
  - id: react
    name: React
    aliases: [reactjs, react.js]
    versions: ["19", "18"]
    sources:
      - {id: react-docs, type: llms_txt, url: "https://example.org/llms.txt", kind: reference, trust: 1}
      - {id: react-blog, type: url_list, urls: ["https://example.org/blog"], kind: guide, trust: 3}
  - id: python
    name: Python
    versions: ["3.12"]
    sources:
      - {id: py-docs, type: llms_txt, url: "https://example.org/py/llms.txt", kind: reference, trust: 1}
"""

_MEMO_BODY = (
    "# useMemo\n\nuseMemo caches a memo value. Pass props and dependencies to the memo hook.\n\n"
    "## Caveats\n\nmemo uses a shallow comparison of props between renders.\n"
)
_TIE_BODY = "# Quokka\n\nThe quokka widget renders a quokka list.\n"

IndexedRoot = namedtuple("IndexedRoot", "root paths db config")


@pytest.fixture
def indexed_root(tmp_path, make_page) -> IndexedRoot:
    from almanac.db import connect
    from almanac.indexer import Indexer
    from almanac.paths import AlmanacPaths
    from almanac.registry import load_config
    from almanac.store import Manifest, Store

    paths = AlmanacPaths(tmp_path)
    paths.config.write_text(_ALMANAC_YAML, encoding="utf-8", newline="\n")
    config = load_config(paths.config)
    store = Store(paths.docs, Manifest(paths.manifest))

    def put(page_id, tech, version, body, **kw):
        store.put(
            make_page(
                id=page_id,
                tech=tech,
                version=version,
                body=body,
                title=page_id.rsplit("/", 1)[1],
                source_url=f"https://example.org/{page_id}",
                **kw,
            )
        )

    put("react/19/memo", "react", "19", _MEMO_BODY)
    put("react/19/tie-trusted", "react", "19", _TIE_BODY, trust=1)
    put("react/19/tie-blog", "react", "19", _TIE_BODY, trust=3, source_id="react-blog")
    put("react/19/legacy", "react", "19", "# Legacy\n\nThe legacy findDOMNode API.\n", flags=["deprecated"])
    put("react/18/memo", "react", "18", "# memo 18\n\nIn React 18 memo wraps a component.\n")
    put("python/3.12/lists", "python", "3.12", "# Lists\n\nA list is a mutable sequence.\n", source_id="py-docs")
    store.manifest.save()
    conn = connect(paths.db)
    Indexer(conn, store, config).index()
    conn.commit()
    conn.close()
    return IndexedRoot(tmp_path, paths, paths.db, config)


@pytest.fixture
def cfg(indexed_root):
    return indexed_root.config


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path


def write_config(root, base_url, extra_sources=None):
    """Write an almanac.yaml with tech ``react`` and a ``react-docs`` llms_txt source at ``base_url``."""
    import json

    sources = [
        {"id": "react-docs", "type": "llms_txt", "url": f"{base_url}/llms.txt", "kind": "reference", "trust": 1},
        *(extra_sources or []),
    ]
    config = {
        "settings": {"allow_private_network": True, "rate_limit_per_host_rps": 100},
        "technologies": [{"id": "react", "name": "React", "versions": ["19"], "sources": sources}],
    }
    (root / "almanac.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8", newline="\n")  # JSON is YAML
