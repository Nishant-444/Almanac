"""Turn fetched raw HTML or markdown into a clean, versioned Page.

Fetched content is untrusted: hidden text and comments are stripped so they
cannot carry prompt injection into the knowledge base.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Comment
from bs4.element import Tag
from markdownify import markdownify

from almanac.models import Page, RawDoc, Source, Technology
from almanac.store import content_hash, identity_url

_VERSION_SEG = re.compile(r"^v?\d+(\.\d+)*$")
_CHARSET_HEADER = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_CHARSET_META = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```|~~~)")
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_DEPRECATED = re.compile(r"\b(deprecated|legacy|removed in)\b", re.IGNORECASE)
_H1 = re.compile(r"^# +(.+?)\s*$", re.MULTILINE)
_HEADING_LINE = re.compile(r"^#{1,6} .*$", re.MULTILINE)
_ID_SUFFIX = re.compile(r"\s*\{#[^}]*\}\s*$")
_BOILERPLATE_TOKEN = re.compile(
    r"^(cookie.*|consent.*|ads?|advert.*|sidebar|breadcrumbs?|toc|table-of-contents)$", re.IGNORECASE
)
_ZERO = r"0(?:\.0+)?(?:px|em|rem|%|pt)?(?:!important)?(?:;|$)"
_HIDDEN_STYLE = re.compile(
    r"display:none|visibility:hidden"
    rf"|(?<![\w-])(?:font-size|width|height):{_ZERO}"
    rf"|(?<![\w-])opacity:0(?:\.0+)?(?:!important)?(?:;|$)"
    r"|(?<![\w-])color:transparent"
    r"|(?<![\w-])(?:left|top|text-indent):-\d{4,}"
    r"|(?<![\w-])clip:rect\(0(?:px)?,0(?:px)?,0(?:px)?,0(?:px)?\)"
)
_OPEN_TAG = re.compile(r"<([A-Za-z][\w:-]*)(?:\s[^<>]*)?>")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_BOILERPLATE_TAGS = ["nav", "header", "footer", "aside", "form", "button"]
_BOILERPLATE_ROLES = {"navigation", "banner", "contentinfo"}


def detect_version(url: str, source: Source, tech: Technology) -> str:
    for seg in urlparse(url).path.split("/"):
        if _VERSION_SEG.match(seg) and seg.removeprefix("v") in tech.versions:
            return seg.removeprefix("v")
    if source.version:
        return source.version
    return tech.stored_version()


def clean(raw: RawDoc, source: Source, tech: Technology, page_id: str, min_content_chars: int = 200) -> Page:
    is_html = "html" in raw.content_type.lower()
    description = canonical = licence = None
    title: str | None = None
    if is_html:
        soup = BeautifulSoup(_decode(raw), "html.parser")
        title, description, canonical, licence = _head_meta(soup, raw.final_url)
        body = _html_to_markdown(soup, raw.final_url)
    else:
        body = _strip_md_comments(_decode(raw).lstrip("\ufeff"))
    body = _normalise(body)
    if not title:
        m = _H1.search(_outside_fences(body))
        title = _ID_SUFFIX.sub("", m.group(1)) if m else _url_title(raw.final_url)

    flags: list[str] = []
    headings = _HEADING_LINE.findall(_outside_fences(body))
    if any(_DEPRECATED.search(s) for s in [title, *headings, body[:300]]):
        flags.append("deprecated")
    if len(body) < min_content_chars:
        flags.append("suspect")

    return Page(
        id=page_id,
        tech=tech.id,
        version=detect_version(identity_url(raw.final_url, raw.extra), source, tech),
        kind=source.kind,
        trust=source.trust,
        title=title,
        source_url=raw.final_url,
        source_id=raw.source_id,
        fetched_at=raw.fetched_at,
        content_hash=content_hash(body),
        last_modified=raw.last_modified,
        flags=flags,
        description=description,
        canonical_url=canonical,
        licence=licence,
        extra=dict(raw.extra),
        body=body,
    )


def _decode(raw: RawDoc) -> str:
    candidates: list[str] = []
    if m := _CHARSET_HEADER.search(raw.content_type):
        candidates.append(m.group(1))
    if m := _CHARSET_META.search(raw.body[:4096]):
        candidates.append(m.group(1).decode("ascii", "ignore"))
    for enc in candidates:
        try:
            return raw.body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.body.decode("utf-8", errors="replace")


def _head_meta(soup: BeautifulSoup, base: str) -> tuple[str | None, str | None, str | None, str | None]:
    title = soup.title.get_text(strip=True) if soup.title else None
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.IGNORECASE)})
    description = str(meta.get("content", "")).strip() or None if isinstance(meta, Tag) else None
    canon = soup.find("link", rel="canonical")
    canonical = urljoin(base, str(canon["href"])) if isinstance(canon, Tag) and canon.get("href") else None
    lic = soup.find("link", rel="license")
    licence = str(lic["href"]) if isinstance(lic, Tag) and lic.get("href") else None
    return title or None, description, canonical, licence


def _is_hidden(tag: Tag) -> bool:
    if tag.has_attr("hidden") or str(tag.get("aria-hidden", "")).lower() == "true":
        return True
    style = re.sub(r"\s+", "", str(tag.get("style", ""))).lower()
    return bool(style and _HIDDEN_STYLE.search(style))


def _is_boilerplate(tag: Tag) -> bool:
    if tag.name in _BOILERPLATE_TAGS or str(tag.get("role", "")).lower() in _BOILERPLATE_ROLES:
        return True
    classes = tag.get("class") or []
    tokens = [*([classes] if isinstance(classes, str) else classes), str(tag.get("id", ""))]
    return any(_BOILERPLATE_TOKEN.match(t) for t in tokens if t)


def _remove(root: Tag, predicate) -> None:
    for tag in root.find_all(predicate):
        if not tag.decomposed:
            tag.decompose()


def _html_to_markdown(soup: BeautifulSoup, base: str) -> str:
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    _remove(soup, lambda t: t.name in {"script", "style", "template", "noscript"})
    _remove(soup, _is_hidden)
    main = soup.select_one("main, article, [role=main], #content, .content") or soup.body or soup
    _remove(main, _is_boilerplate)
    for tag in main.find_all(["a", "img"]):
        attr = "href" if tag.name == "a" else "src"
        if tag.has_attr(attr):
            tag[attr] = urljoin(base, str(tag[attr]))
    for h in main.find_all(re.compile(r"^h[1-6]$")):
        if h.get("id"):
            h.append(f" {{#{h['id']}}}")
    return markdownify(str(main), heading_style="ATX", code_language_callback=_code_language)


def _code_language(el: Tag) -> str | None:
    for node in [el, *el.find_all("code")]:
        classes = node.get("class") or []
        for cls in [classes] if isinstance(classes, str) else classes:
            for prefix in ("language-", "lang-"):
                if cls.startswith(prefix):
                    return cls[len(prefix) :]
    return None


def _split_fences(text: str) -> list[tuple[bool, str]]:
    """Split into (is_fenced, text) segments, keeping line endings."""
    segments: list[tuple[bool, list[str]]] = []
    fenced = False
    for line in text.splitlines(keepends=True):
        opens = bool(_FENCE.match(line))
        if opens and not fenced:
            fenced = True
            segments.append((True, [line]))
            continue
        if not segments or segments[-1][0] != fenced:
            segments.append((fenced, []))
        segments[-1][1].append(line)
        if opens and fenced:
            fenced = False
    return [(f, "".join(lines)) for f, lines in segments]


def _strip_md_comments(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(seg if fenced else _strip_hidden_html(_COMMENT.sub("", seg)) for fenced, seg in _split_fences(text))


def _strip_hidden_html(text: str) -> str:
    """Remove hidden inline HTML elements (with content) from unfenced markdown.

    An unclosed hidden element is removed through the end of the segment.
    """
    pos = 0
    while m := _OPEN_TAG.search(text, pos):
        name = m.group(1).lower()
        tag = BeautifulSoup(m.group(0), "html.parser").find()
        if not isinstance(tag, Tag) or not _is_hidden(tag):
            pos = m.end()
            continue
        end = m.end()
        if name not in _VOID and not m.group(0).endswith("/>"):
            depth = 1
            tags = re.compile(rf"<(/?){re.escape(name)}(?=[\s/>])[^<>]*>", re.IGNORECASE)
            end = len(text)
            for t in tags.finditer(text, m.end()):
                depth += -1 if t.group(1) else 1
                if depth == 0:
                    end = t.end()
                    break
        text = text[: m.start()] + text[end:]
        pos = m.start()
    return text


def _outside_fences(text: str) -> str:
    return "".join(seg for fenced, seg in _split_fences(text) if not fenced)


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for fenced, seg in _split_fences(text):
        if fenced:
            out.append(seg)
        else:
            seg = "\n".join(line.rstrip() for line in seg.split("\n"))
            out.append(re.sub(r"\n{3,}", "\n\n", seg))
    joined = "".join(out)
    # fenced/unfenced joins can reintroduce long blank runs only outside fences
    return joined.strip("\n") + "\n" if joined.strip() else ""


def _url_title(url: str) -> str:
    segs = [s for s in urlparse(url).path.split("/") if s]
    return unquote(segs[-1]) if segs else (urlparse(url).hostname or url)
