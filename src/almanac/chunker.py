"""Split a stored markdown page into heading-bounded chunks (the unit of search)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from almanac.models import Page

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
# An explicit heading id: ``{#id}`` (Markdown extensions) or ``{/*id*/}`` (MDX, e.g. react.dev).
_ID_RE = re.compile(r"\s*\{(?:#([^}\s]+)|/\*\s*([^*\s]+)\s*\*/)\}\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


@dataclass
class Chunk:
    page_id: str
    ord: int
    heading_path: str
    anchor: str
    text: str
    tokens: int


@dataclass
class _Block:
    text: str
    code: bool
    lead: int = 0  # characters of heading prefix at the start of text


@dataclass
class _Section:
    heading_path: str
    anchor: str
    heading_line: str  # "" for text before the first heading
    body: list[str]


def _fence_close_ok(line: str, opener: str) -> bool:
    m = _FENCE_RE.match(line)
    return bool(m) and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener) and not m.group(2).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", text.lower()).replace(" ", "-")


def heading_anchor(heading_text: str) -> str:
    """Anchor for a heading's text: an explicit ``{#id}``/``{/*id*/}`` first, otherwise the slug."""
    id_match = _ID_RE.search(heading_text)
    if id_match:
        return id_match.group(1) or id_match.group(2)
    return _slug(heading_text.strip())


def _sections(page: Page) -> list[_Section]:
    sections = [_Section(page.title, "", "", [])]
    stack: list[tuple[int, str]] = []
    opener: str | None = None
    for line in page.body.splitlines():
        if opener is not None:
            sections[-1].body.append(line)
            if _fence_close_ok(line, opener):
                opener = None
            continue
        fence = _FENCE_RE.match(line)
        if fence:
            opener = fence.group(1)
            sections[-1].body.append(line)
            continue
        heading = _HEADING_RE.match(line)
        if not heading:
            sections[-1].body.append(line)
            continue
        level, raw = len(heading.group(1)), heading.group(2)
        title = _ID_RE.sub("", raw).strip()
        anchor = heading_anchor(raw)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = " > ".join(t for _, t in stack)
        # The id marker only feeds the anchor; users never see it in chunk text.
        sections.append(_Section(path, anchor, _ID_RE.sub("", line), []))
    return sections


def _blocks(lines: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    para: list[str] = []
    fence: list[str] = []
    opener: str | None = None

    def flush_para() -> None:
        if para:
            blocks.append(_Block("\n".join(para), False))
            para.clear()

    for line in lines:
        if opener is not None:
            fence.append(line)
            if _fence_close_ok(line, opener):
                blocks.append(_Block("\n".join(fence), True))
                fence.clear()
                opener = None
            continue
        m = _FENCE_RE.match(line)
        if m:
            flush_para()
            opener = m.group(1)
            fence.append(line)
        elif not line.strip():
            flush_para()
        else:
            para.append(line)
    flush_para()
    if fence:
        blocks.append(_Block("\n".join(fence), True))
    return blocks


def _pack(pieces: list[str], limit: int, sep: str) -> list[str]:
    """Greedily join pieces with sep, keeping each group within limit tokens where possible."""
    groups: list[str] = []
    cur = ""
    for piece in pieces:
        for part in _hard_split(piece, limit):
            joined = f"{cur}{sep}{part}" if cur else part
            if cur and estimate_tokens(joined) > limit:
                groups.append(cur)
                cur = part
            else:
                cur = joined
    if cur:
        groups.append(cur)
    return groups


def _hard_split(text: str, limit: int) -> list[str]:
    """Last resort for a single unit larger than limit: cut at word boundaries, then characters."""
    if estimate_tokens(text) <= limit:
        return [text]
    size = max(limit * 4, 1)
    out: list[str] = []
    while len(text) > size:
        cut = text.rfind(" ", 0, size)
        cut = cut if cut > 0 else size
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def _split_code(block: _Block, limit: int) -> list[_Block]:
    lines = block.text.split("\n")
    opener_line = lines[0]
    fence = _FENCE_RE.match(opener_line).group(1)  # type: ignore[union-attr]
    body = lines[1:]
    if body and _fence_close_ok(body[-1], fence):
        body = body[:-1]
    closer = fence
    overhead = estimate_tokens(opener_line) + estimate_tokens(closer) + 2
    inner = max(limit - overhead, 1)
    groups: list[str] = []
    cur: list[str] = []
    for chunk in "\n".join(body).split("\n\n"):
        groups.append(chunk)
        cur = []
    del cur
    # Lines within a group that is itself too large are split per line.
    pieces: list[str] = []
    for g in groups:
        if estimate_tokens(g) <= inner:
            pieces.append(g)
        else:
            pieces.extend(_pack(g.split("\n"), inner, "\n"))
    parts = _pack(pieces, inner, "\n\n")
    return [_Block(f"{opener_line}\n{p}\n{closer}", True) for p in parts]


def _split_block(block: _Block, hard_max: int, limit: int) -> list[_Block]:
    if estimate_tokens(block.text) <= hard_max:
        return [block]
    if block.code:
        return _split_code(block, limit)
    sentences = _SENTENCE_RE.split(block.text)
    return [_Block(t, False) for t in _pack(sentences, limit, " ")]


_MAX_OVERLAP_CHARS = 200  # 50 tokens


def _last_sentence(block: _Block) -> str:
    """Bounded overlap: last sentence (or last line) of the block, never the heading, at most 50 tokens."""
    if block.code:
        return ""
    sentence = _SENTENCE_RE.split(block.text[block.lead :].strip())[-1]
    lines = [ln.strip() for ln in sentence.splitlines() if ln.strip()]
    sentence = lines[-1] if lines else ""
    if len(sentence) > _MAX_OVERLAP_CHARS:
        tail = sentence[-_MAX_OVERLAP_CHARS:]
        if not sentence[-_MAX_OVERLAP_CHARS - 1].isspace():
            tail = tail.partition(" ")[2]
        sentence = tail.strip()
    return sentence


def _chunk_section(sec: _Section, target: int, hard_max: int) -> list[tuple[str, int]]:
    """Return the section's chunk texts (overlap included) with token counts."""
    body = "\n".join(sec.body).strip()
    if not body:
        return []
    full = f"{sec.heading_line}\n\n{body}" if sec.heading_line else body
    if estimate_tokens(full) <= target:
        return [(full, estimate_tokens(full))]

    blocks = _blocks(sec.body)
    prefix = f"{sec.heading_line}\n\n" if sec.heading_line else ""
    reserve = estimate_tokens(prefix)
    pieces: list[_Block] = []
    for i, block in enumerate(blocks):
        if i == 0 and prefix:
            first = _Block(prefix + block.text, block.code, len(prefix))
            if estimate_tokens(first.text) <= hard_max:
                pieces.append(first)
                continue
            parts = _split_block(block, hard_max - reserve, max(target - reserve, 1))
            parts[0] = _Block(prefix + parts[0].text, parts[0].code, len(prefix))
            pieces.extend(parts)
        else:
            pieces.extend(_split_block(block, hard_max, target))

    groups: list[list[_Block]] = []
    cur: list[_Block] = []
    for piece in pieces:
        if cur and estimate_tokens("\n\n".join(b.text for b in cur + [piece])) > target:
            groups.append(cur)
            cur = []
        cur.append(piece)
    if cur:
        groups.append(cur)

    out: list[tuple[str, int]] = []
    for i, group in enumerate(groups):
        text = "\n\n".join(b.text for b in group)
        if i > 0:
            overlap = _last_sentence(groups[i - 1][-1])
            prev_text = out[-1][0]
            if overlap and overlap != prev_text:
                with_overlap = f"{overlap}\n\n{text}"
                if estimate_tokens(with_overlap) <= hard_max:
                    text = with_overlap
        out.append((text, estimate_tokens(text)))
    return out


def chunk_page(page: Page, target: int = 600, hard_max: int = 900) -> list[Chunk]:
    chunks: list[Chunk] = []
    for sec in _sections(page):
        for text, tokens in _chunk_section(sec, target, hard_max):
            chunks.append(Chunk(page.id, len(chunks), sec.heading_path, sec.anchor, text, tokens))
    return chunks
