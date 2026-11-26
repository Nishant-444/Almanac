import itertools

from almanac.chunker import chunk_page, estimate_tokens


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcde") == 2


def test_heading_paths_and_anchors(make_page):
    body = "# Hooks\n\n## useMemo {#use-memo}\n\nCaches a value.\n\n### Caveats\n\nOnly in components.\n"
    cs = chunk_page(make_page(body=body))
    assert [(c.heading_path, c.anchor) for c in cs] == [
        ("Hooks > useMemo", "use-memo"),
        ("Hooks > useMemo > Caveats", "caveats"),
    ]
    assert cs[0].text.startswith("## useMemo") and [c.ord for c in cs] == [0, 1]
    assert all(c.page_id == "react/19/memo" for c in cs)


def test_slug_anchor_strips_punctuation(make_page):
    cs = chunk_page(make_page(body="## Using `useMemo`, safely!\n\nText.\n"))
    assert cs[0].anchor == "using-usememo-safely"


def test_text_before_first_heading_uses_title(make_page):
    cs = chunk_page(make_page(body="Intro text.\n\n## A\n\nBody.\n"))
    assert (cs[0].heading_path, cs[0].anchor, cs[0].text) == ("useMemo", "", "Intro text.")
    assert chunk_page(make_page(body="\n\n## A\n\nBody.\n"))[0].heading_path == "A"


def test_heading_inside_code_is_not_a_heading(make_page):
    body = "## A\n\n```py\n# comment\nx = 1\n```\n\n~~~\n## nope\n~~~\n"
    cs = chunk_page(make_page(body=body))
    assert len(cs) == 1 and "# comment" in cs[0].text and "## nope" in cs[0].text


def test_unclosed_fence_runs_to_end(make_page):
    cs = chunk_page(make_page(body="## A\n\n```\ncode\n## B\nmore\n"))
    assert len(cs) == 1 and "## B" in cs[0].text


def test_code_block_kept_whole(make_page):
    code = "```py\n" + "\n".join(f"x{i} = {i}" for i in range(160)) + "\n```"
    assert 350 <= estimate_tokens(code) <= 450
    para = "Word " * 150
    body = f"## S\n\n{para}\n\n{code}\n\n{para}\n"
    cs = chunk_page(make_page(body=body))
    assert len(cs) > 1
    assert any(code in c.text for c in cs)
    assert all(c.text.count("```") % 2 == 0 for c in cs)


def test_oversized_code_split_with_fences(make_page):
    lines = [f"value_{i} = compute({i})" for i in range(300)]
    code = "```py\n" + "\n\n".join("\n".join(lines[i : i + 5]) for i in range(0, 300, 5)) + "\n```"
    assert estimate_tokens(code) >= 1500
    cs = chunk_page(make_page(body=f"## Big\n\n{code}\n"))
    assert len(cs) >= 2
    for c in cs:
        assert "```py\n" in c.text and c.text.rstrip().endswith("```")
        assert c.tokens <= 900
    assert cs[0].text.startswith("## Big\n\n```py")
    assert not cs[1].text.startswith("## Big")


def test_long_section_respects_hard_max_and_overlaps(make_page):
    paras = [f"Sentence {i} alpha beta gamma delta epsilon. Last of para {i}." for i in range(200)]
    cs = chunk_page(make_page(body="## Long\n\n" + "\n\n".join(paras)))
    assert len(cs) >= 5 and all(c.tokens <= 900 for c in cs)
    assert all(c.heading_path == "Long" and c.anchor == "long" for c in cs)
    assert cs[1].text.startswith("Last of para")
    assert all(c.tokens == estimate_tokens(c.text) for c in cs)
    assert [c.ord for c in cs] == list(range(len(cs)))


def test_no_overlap_after_code(make_page):
    code = "```\n" + "line\n" * 700 + "```"
    cs = chunk_page(make_page(body=f"## S\n\n{code}\n\nAfter the code.\n"))
    assert len(cs) == 2 and cs[1].text == "After the code."


def test_overlap_omitted_when_over_hard_max(make_page):
    big = " ".join(f"w{i}" for i in range(600))  # one huge sentence-free paragraph under hard_max
    body = f"## S\n\nFirst. Second sentence here.\n\n{big}\n"
    cs = chunk_page(make_page(body=body), target=100, hard_max=estimate_tokens(big) + 2)
    assert all(c.tokens <= estimate_tokens(big) + 2 for c in cs)


def test_overlap_bounded_for_unpunctuated_bullets(make_page):
    paras = ["\n".join(f"- item {i}-{j} some words here" for j in range(6)) for i in range(25)]
    cs = chunk_page(make_page(body="## L\n\n" + "\n\n".join(paras)), target=200, hard_max=300)
    assert len(cs) >= 3
    for prev, c in itertools.pairwise(cs):
        prefix = c.text.split("\n\n")[0]
        assert estimate_tokens(prefix) <= 50
        assert prefix != prev.text.split("\n\n")[-1]
        assert not prefix.startswith("## ")


def test_continuation_never_starts_with_heading(make_page):
    big = " ".join(f"w{i}" for i in range(560))  # ~700 tokens, no sentence end
    cs = chunk_page(make_page(body=f"## H\n\n{big}\n\nMore text after. Another sentence.\n"))
    assert len(cs) >= 2
    assert all(not c.text.startswith("## ") for c in cs[1:])
    assert estimate_tokens(cs[1].text.split("\n\n")[0]) <= 50


def test_mdx_heading_id_marker_feeds_anchor_and_is_hidden(make_page):
    body = "## Usage {/*usage*/}\n\nText.\n\n### Skipping with `memo` {/*skipping-with-memo*/}\n\nMore.\n"
    cs = chunk_page(make_page(body=body))
    assert [(c.heading_path, c.anchor) for c in cs] == [
        ("Usage", "usage"),
        ("Usage > Skipping with `memo`", "skipping-with-memo"),
    ]
    assert all("{/*" not in c.text for c in cs)
