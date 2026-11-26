from datetime import UTC, datetime

import pytest

from almanac.cleaner import clean, detect_version
from almanac.models import Kind, RawDoc, Source, Technology
from almanac.store import content_hash

URL = "https://docs.test/reference/19/memo"


@pytest.fixture
def ref_source() -> Source:
    return Source(id="react-docs", type="url_list", urls=[URL], kind=Kind.reference, trust=1)


@pytest.fixture
def react_tech(ref_source) -> Technology:
    return Technology(id="react", name="React", versions=["19", "latest"], sources=[ref_source])


def _raw(body: bytes, content_type: str, url: str = URL) -> RawDoc:
    return RawDoc(
        source_id="react-docs",
        tech_id="react",
        url=url,
        final_url=url,
        body=body,
        content_type=content_type,
        fetched_at=datetime(2026, 11, 24, tzinfo=UTC),
        last_modified="Mon, 01 Jan 2026 00:00:00 GMT",
        extra={"k": "v"},
    )


@pytest.fixture
def article_raw(fixtures) -> RawDoc:
    return _raw((fixtures / "html" / "article.html").read_bytes(), "text/html; charset=utf-8")


def test_html_cleaned(article_raw, react_tech, ref_source):
    page = clean(article_raw, ref_source, react_tech, "react/19/memo")
    b = page.body
    for gone in ["Site Navigation", "© 2026", "We use cookies", "IGNORE PREVIOUS", "x-hidden", "secret comment"]:
        assert gone not in b
    assert "SCRIPT-TEXT" not in b and "Sidebar link" not in b
    assert "```jsx\n" in b and "  return <List items={items} />;" in b
    assert "\n\n  const x = 1;" in b
    assert "| Prop | Type |" in b
    assert "(https://docs.test/reference/api/memo)" in b
    assert "## Caveats {#caveats}" in b
    assert page.title == "memo – Docs" and page.description and page.canonical_url
    assert "deprecated" in page.flags
    assert page.content_hash == content_hash(b) and page.version == "19"
    assert page.id == "react/19/memo" and page.kind == Kind.reference and page.trust == 1
    assert page.source_url == URL and page.source_id == "react-docs" and page.tech == "react"
    assert page.last_modified == "Mon, 01 Jan 2026 00:00:00 GMT"
    assert page.extra == {"k": "v"} and page.extra is not article_raw.extra
    assert page.licence is None


def test_licence_link(react_tech, ref_source):
    html = b'<html><head><link rel="license" href="https://x.test/MIT"></head><body><main>hi</main></body></html>'
    page = clean(_raw(html, "text/html"), ref_source, react_tech, "p")
    assert page.licence == "https://x.test/MIT"


def test_title_falls_back_to_url_segment(react_tech, ref_source):
    raw = _raw(b"<html><body><main><p>text</p></main></body></html>", "text/html")
    assert clean(raw, ref_source, react_tech, "p").title == "memo"


def test_markdown_passthrough_strips_comments_keeps_code(react_tech, ref_source):
    md = "# Title Here\r\n\r\nText <!-- x --> more  \r\n\r\n\r\n\r\n```html\r\n<!-- keep -->\r\n```\r\n<!-- multi\r\nline -->\r\nend\r\n"
    page = clean(_raw(md.encode(), "text/markdown"), ref_source, react_tech, "p")
    assert "\r" not in page.body
    assert "<!-- x -->" not in page.body and "multi" not in page.body
    assert "<!-- keep -->" in page.body
    assert "more  \n" not in page.body and "\n\n\n" not in page.body
    assert page.title == "Title Here"


def test_short_page_flagged_suspect(react_tech, ref_source):
    page = clean(_raw(b"# Hi\n\nshort", "text/markdown"), ref_source, react_tech, "p")
    assert "suspect" in page.flags


def test_charset_from_header(fixtures, react_tech, ref_source):
    data = (fixtures / "html" / "cp1252.html").read_bytes()
    page = clean(_raw(data, "text/html; charset=windows-1252"), ref_source, react_tech, "p")
    assert "café" in page.body and page.title == "Café"


def test_charset_from_meta(react_tech, ref_source):
    data = '<html><head><meta charset="windows-1252"></head><body><main>café</main></body></html>'.encode("cp1252")
    assert "café" in clean(_raw(data, "text/html"), ref_source, react_tech, "p").body


def test_detect_version(ref_source):
    t = Technology(id="react", name="R", versions=["18", "19"], sources=[ref_source])
    latest_only = Technology(id="x", name="X", sources=[ref_source])
    assert detect_version("https://d.test/v18/ref/x", ref_source, t) == "18"
    assert detect_version("https://d.test/ref/x", ref_source, t) == "18"
    assert detect_version("https://d.test/ref/x", ref_source.model_copy(update={"version": "19"}), t) == "19"
    assert detect_version("https://d.test/ref/2024", ref_source, latest_only) == "latest"


HIDDEN_STYLES = [
    "display:none",
    "DISPLAY: NONE",
    "display : none",
    "visibility:hidden",
    "font-size:0",
    "font-size:0!important",
    "font-size: 0 !important",
    "font-size:0.0em",
    "width:0!important",
    "height:0px",
    "left:-9999px",
    "left:-10000px",
    "top:-9999px",
    "text-indent:-9999px",
    "left:-5000px",
    "opacity:0",
    "opacity:0!important",
    "color:transparent",
    "clip:rect(0,0,0,0)",
    "clip:rect(0px,0px,0px,0px)",
]
VISIBLE_STYLES = ["opacity:1", "left:-10px", "font-size:0.9em", "width:10px", "max-width:0"]


@pytest.mark.parametrize("style", HIDDEN_STYLES)
def test_hidden_style_variants_removed(style, react_tech, ref_source):
    html = f'<html><body><main><p>visible</p><div style="{style}">SECRET-INJECT</div></main></body></html>'
    page = clean(_raw(html.encode(), "text/html"), ref_source, react_tech, "p")
    assert "SECRET-INJECT" not in page.body and "visible" in page.body


@pytest.mark.parametrize("style", HIDDEN_STYLES)
def test_hidden_style_variants_removed_in_markdown(style, react_tech, ref_source):
    md = f'# T\n\nvisible\n\n<div style="{style}">SECRET-INJECT</div>\n\nafter\n'
    page = clean(_raw(md.encode(), "text/markdown"), ref_source, react_tech, "p")
    assert "SECRET-INJECT" not in page.body and "after" in page.body


@pytest.mark.parametrize("style", VISIBLE_STYLES)
def test_visible_styles_kept(style, react_tech, ref_source):
    html = f'<html><body><main><div style="{style}">KEEP-ME</div></main></body></html>'
    assert "KEEP-ME" in clean(_raw(html.encode(), "text/html"), ref_source, react_tech, "p").body
    md = f'# T\n\n<div style="{style}">KEEP-ME</div>\n'
    assert "KEEP-ME" in clean(_raw(md.encode(), "text/markdown"), ref_source, react_tech, "p").body


def test_markdown_hidden_html_removed_outside_fences_only(react_tech, ref_source):
    md = (
        "# T\n\nbefore <span hidden>H-SPAN</span> after\n\n"
        '<div aria-hidden="true">\n<div>nested</div>H-ARIA\n</div>\n\nmid\n\n'
        'Press <kbd>Ctrl</kbd> now.\n\n```html\n<div style="display:none">IN-FENCE</div>\n```\n'
    )
    body = clean(_raw(md.encode(), "text/markdown"), ref_source, react_tech, "p").body
    for gone in ["H-SPAN", "H-ARIA", "nested"]:
        assert gone not in body
    assert "before  after" in body or "before after" in body
    assert "mid" in body and "<kbd>Ctrl</kbd>" in body
    assert '<div style="display:none">IN-FENCE</div>' in body


def _md(ref_source, react_tech, md: str, url: str = URL):
    return clean(_raw(md.encode(), "text/markdown", url), ref_source, react_tech, "p")


def test_markdown_front_matter_gives_title_and_is_removed(ref_source, react_tech):
    md = "---\ntitle: memo\n---\n\n`memo` lets you skip re-rendering a component when its props are unchanged.\n"
    page = _md(ref_source, react_tech, md)
    assert page.title == "memo"
    assert not page.body.startswith("---") and "title: memo" not in page.body


def test_url_title_drops_file_extension(ref_source, react_tech):
    page = _md(ref_source, react_tech, "No heading here.\n", url="https://docs.test/reference/memo.md")
    assert page.title == "memo"


def test_mdx_component_tag_lines_are_removed_but_content_kept(ref_source, react_tech):
    md = (
        "## Example\n\n<Sandpack>\n\n```js\n<Sandpack>\nconst x = 1;\n```\n\n</Sandpack>\n\n<Solution />\n\n"
        '<Recipes titleText="Examples">\n\nWrap `<Suspense>` around it.\n\n</Recipes>\n'
    )
    body = _md(ref_source, react_tech, md).body
    assert "</Sandpack>" not in body and "<Solution />" not in body and "<Recipes" not in body
    assert "```js\n<Sandpack>\nconst x = 1;\n```" in body  # code is verbatim
    assert "Wrap `<Suspense>` around it." in body  # inline JSX in prose is kept


def test_mdx_fence_meta_braces_are_dropped(ref_source, react_tech):
    md = "## A\n\n```js {expectedErrors: {'react-compiler': [5, 6]}} src/List.js\nlet a;\n```\n"
    body = _md(ref_source, react_tech, md).body
    assert "```js src/List.js\nlet a;\n```" in body
