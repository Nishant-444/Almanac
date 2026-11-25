from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from almanac.db import connect
from almanac.errors import FetchBlocked, FetchFailed
from almanac.fetch.fetchlog import FetchLog
from almanac.fetch.git import GitFetcher
from almanac.models import Settings, Source, Technology
from almanac.store import Manifest

TECH = Technology(id="python", name="Python", sources=[])


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def rev_parse(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def write(repo: Path, rel: str, data: bytes) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def commit(repo: Path, msg: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", msg)


def git_src(url, **kw) -> Source:
    if isinstance(url, Path):
        url = url.as_uri()
    return Source(id="py-git", type="git", url=url, kind="reference", trust=1, **kw)


@pytest.fixture
def local_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "T")
    git(repo, "config", "user.email", "t@example.org")
    write(repo, "docs/guide.md", b"# Guide\n\nline\n")
    write(repo, "docs/img.png", b"\x89PNG")
    write(repo, "src/x.py", b"print(1)\n")
    commit(repo, "init")
    return repo


@pytest.fixture
def manifest(tmp_path):
    return Manifest.load(tmp_path / "manifest.json")


@pytest.fixture
def make_fetcher(tmp_path):
    conns = []

    def factory(**settings):
        conn = connect(tmp_path / "t.db")
        conns.append(conn)
        f = GitFetcher(Settings(**settings), tmp_path / "cache", FetchLog(conn, "run1"))

        def rows():
            return [dict(r) for r in conn.execute("select * from fetch_log order by id")]

        f.rows = rows
        return f

    yield factory
    for c in conns:
        c.close()


@pytest.fixture
def git_fetcher(make_fetcher):
    return make_fetcher(allow_private_network=True)


def test_git_first_fetch_records_sha(local_repo, git_fetcher, manifest):
    docs = list(git_fetcher.fetch(git_src(local_repo, paths=["docs"]), TECH, manifest))
    assert [d.extra["repo_path"] for d in docs] == ["docs/guide.md"]
    assert docs[0].extra["commit_sha"] == rev_parse(local_repo)
    assert docs[0].content_type == "text/markdown"
    assert docs[0].body == b"# Guide\n\nline\n"
    assert docs[0].url.endswith("/docs/guide.md")
    assert manifest.source_state("py-git")["commit_sha"] == rev_parse(local_repo)
    rows = git_fetcher.rows()
    assert len(rows) == 1 and rows[0]["error"] is None and rows[0]["status"] is None and rows[0]["bytes"] == 0


def test_git_no_paths_yields_only_doc_types(local_repo, git_fetcher, manifest):
    write(local_repo, "notes.txt", b"t\n")
    write(local_repo, "a.rst", b"r\n")
    commit(local_repo, "more")
    docs = list(git_fetcher.fetch(git_src(local_repo), TECH, manifest))
    got = {d.extra["repo_path"]: d.content_type for d in docs}
    assert got == {"docs/guide.md": "text/markdown", "notes.txt": "text/plain", "a.rst": "text/plain"}


def test_git_unchanged_then_new_commit(local_repo, git_fetcher, manifest):
    s = git_src(local_repo, paths=["docs"])
    assert len(list(git_fetcher.fetch(s, TECH, manifest))) == 1
    assert list(git_fetcher.fetch(s, TECH, manifest)) == []
    assert len(list(git_fetcher.fetch(s, TECH, manifest, force=True))) == 1
    write(local_repo, "docs/guide.md", b"# Guide\n\nchanged\n")
    commit(local_repo, "change")
    docs = list(git_fetcher.fetch(s, TECH, manifest))
    assert len(docs) == 1
    assert docs[0].extra["commit_sha"] == rev_parse(local_repo)
    assert b"changed" in docs[0].body


def test_git_sha_recorded_only_after_all_yielded(local_repo, git_fetcher, manifest):
    gen = git_fetcher.fetch(git_src(local_repo, paths=["docs"]), TECH, manifest)
    next(gen)
    assert "commit_sha" not in manifest.source_state("py-git")
    gen.close()
    assert "commit_sha" not in manifest.source_state("py-git")


def test_git_local_blocked_without_opt_in(local_repo, make_fetcher, manifest):
    f = make_fetcher(allow_private_network=False)
    for url in (local_repo.as_uri(), str(local_repo)):
        with pytest.raises(FetchBlocked) as e:
            list(f.fetch(git_src(url), TECH, manifest))
        assert e.value.reason == "scheme"
    assert [r["error"] for r in f.rows()] == ["scheme", "scheme"]


def test_git_rejects_option_like_url(git_fetcher, manifest):
    with pytest.raises(FetchBlocked) as e:
        list(git_fetcher.fetch(git_src("--upload-pack=evil"), TECH, manifest))
    assert e.value.reason == "scheme"


def test_git_failure_is_fetchfailed(git_fetcher, manifest, tmp_path):
    with pytest.raises(FetchFailed) as e:
        list(git_fetcher.fetch(git_src(tmp_path / "nope"), TECH, manifest))
    assert e.value.reason.startswith("git:")
    assert git_fetcher.rows()[0]["error"].startswith("git:")


def test_git_github_citation_url(local_repo, git_fetcher, monkeypatch):
    from almanac.fetch.git import citation_url

    assert citation_url("https://github.com/o/r.git", "abc", "docs/a.md") == "https://github.com/o/r/blob/abc/docs/a.md"
    assert citation_url("https://github.com/o/r", "abc", "a.md") == "https://github.com/o/r/blob/abc/a.md"
    assert citation_url("https://git.example.org/r/", "abc", "a.md") == "https://git.example.org/r/a.md"


def test_git_stable_url_has_no_sha():
    from almanac.fetch.git import stable_url

    assert stable_url("https://github.com/o/r.git", "docs/a.md") == "https://github.com/o/r/docs/a.md"
    assert stable_url("https://git.example.org/r/", "a.md") == "https://git.example.org/r/a.md"


def test_git_skips_symlink_escaping_repo(local_repo, git_fetcher, manifest, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("TOP SECRET", encoding="utf-8")
    try:
        os.symlink(secret, local_repo / "docs" / "leak.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    git(local_repo, "-c", "core.symlinks=true", "add", "-A")
    git(local_repo, "commit", "-m", "link")
    docs = list(git_fetcher.fetch(git_src(local_repo, paths=["docs"]), TECH, manifest))
    assert all(b"TOP SECRET" not in d.body for d in docs)
    assert [d.extra["repo_path"] for d in docs] == ["docs/guide.md"]


def test_git_symlink_entry_never_leaks_outside_file(local_repo, git_fetcher, manifest, tmp_path):
    """Commit a mode-120000 entry via the index so this runs even where os.symlink is unavailable."""
    secret = tmp_path / "secret.md"
    secret.write_text("TOP SECRET", encoding="utf-8")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=local_repo,
        input=str(secret),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git(local_repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},docs/leak.md")
    git(local_repo, "commit", "-m", "link")
    docs = list(git_fetcher.fetch(git_src(local_repo, paths=["docs"]), TECH, manifest))
    assert all(b"TOP SECRET" not in d.body for d in docs)
    leak = [d for d in docs if d.extra["repo_path"] == "docs/leak.md"]
    # real symlink checkout -> skipped; symlink-less checkout -> plain file holding only the target path
    assert not leak or leak[0].body == str(secret).encode()


def test_git_blocks_credentialed_url_and_logs_without_secret(git_fetcher, manifest):
    for url in ("https://u:p@docs.test/r.git", "file://u:p@localhost/x"):
        with pytest.raises(FetchBlocked) as e:
            list(git_fetcher.fetch(git_src(url), TECH, manifest))
        assert e.value.reason == "credentials"
    rows = git_fetcher.rows()
    assert [r["url"] for r in rows] == ["https://docs.test/r.git", "file://localhost/x"]
    assert all("u:p" not in r["url"] for r in rows)


def test_git_failed_first_run_removes_clone_and_retry_works(local_repo, make_fetcher, tmp_path, manifest):
    def failing(cmd, **kw):
        if "checkout" in cmd and "sparse-checkout" not in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="boom\nmore")
        return subprocess.run(cmd, check=True, **{k: v for k, v in kw.items() if k != "check"})

    settings = Settings(allow_private_network=True)
    conn = connect(tmp_path / "t2.db")
    bad = GitFetcher(settings, tmp_path / "cache", FetchLog(conn, "r"), run_git=failing)
    s = git_src(local_repo, paths=["docs"])
    with pytest.raises(FetchFailed, match="git: boom"):
        list(bad.fetch(s, TECH, manifest))
    assert not (tmp_path / "cache" / "py-git").exists()
    docs = list(make_fetcher(allow_private_network=True).fetch(s, TECH, manifest))
    assert [d.extra["repo_path"] for d in docs] == ["docs/guide.md"]
    conn.close()


def test_git_paths_anchored_and_changes_respected(local_repo, git_fetcher, manifest):
    write(local_repo, "src/docs/x.md", b"x\n")
    write(local_repo, "src/y.md", b"y\n")
    commit(local_repo, "more")
    got = lambda **kw: sorted(
        d.extra["repo_path"] for d in git_fetcher.fetch(git_src(local_repo, **kw), TECH, manifest, force=True)
    )
    assert got(paths=["docs"]) == ["docs/guide.md"]
    assert got(paths=["src"]) == ["src/docs/x.md", "src/y.md"]
    assert got() == ["docs/guide.md", "src/docs/x.md", "src/y.md"]


def test_git_untracked_files_not_yielded(local_repo, git_fetcher, manifest, tmp_path):
    s = git_src(local_repo, paths=["docs"])
    list(git_fetcher.fetch(s, TECH, manifest))
    write(tmp_path / "cache" / "py-git", "docs/stray.md", b"stray\n")
    docs = list(git_fetcher.fetch(s, TECH, manifest, force=True))
    assert [d.extra["repo_path"] for d in docs] == ["docs/guide.md"]


@pytest.mark.parametrize("bad_id", ["../x", ""])
def test_git_bad_source_id_never_deletes_outside_cache(local_repo, git_fetcher, manifest, tmp_path, bad_id):
    target = (tmp_path / "cache" / bad_id).resolve()
    target.mkdir(parents=True, exist_ok=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    src = Source.model_construct(
        id=bad_id, type="git", url=local_repo.as_uri(), kind="reference", trust=1, paths=[], branch=None
    )
    with pytest.raises(FetchBlocked) as e:
        list(git_fetcher.fetch(src, TECH, manifest))
    assert e.value.reason == "bad-source-id"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def _other_repo(tmp_path, body: bytes) -> Path:
    repo = tmp_path / "other"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "T")
    git(repo, "config", "user.email", "t@example.org")
    write(repo, "docs/guide.md", body)
    commit(repo, "other")
    return repo


def test_git_url_change_refetches_new_remote(local_repo, git_fetcher, manifest, tmp_path):
    """Q8: same source id, new url -> the existing clone follows the new remote."""
    list(git_fetcher.fetch(git_src(local_repo, paths=["docs"]), TECH, manifest))
    other = _other_repo(tmp_path, b"# Other\n\nfrom the new remote\n")
    docs = list(git_fetcher.fetch(git_src(other, paths=["docs"]), TECH, manifest))
    assert [d.body for d in docs] == [b"# Other\n\nfrom the new remote\n"]


def test_git_calls_are_hardened(local_repo, tmp_path, manifest):
    """Q13/Q19: every git call disables redirects, enables long paths and ignores the user's global config."""
    calls = []

    def spy(cmd, **kw):
        calls.append((cmd, kw["env"]))
        return subprocess.run(cmd, check=kw.pop("check", True), **kw)

    conn = connect(tmp_path / "h.db")
    f = GitFetcher(Settings(allow_private_network=True), tmp_path / "cache", FetchLog(conn, "r"), run_git=spy)
    s = git_src(local_repo, paths=["docs"])
    list(f.fetch(s, TECH, manifest))
    list(f.fetch(s, TECH, manifest, force=True))  # existing-clone path too
    conn.close()
    assert calls
    for cmd, env in calls:
        assert cmd[:7] == [
            "git", "-c", "core.autocrlf=false", "-c", "http.followRedirects=false", "-c", "core.longpaths=true",
        ]  # fmt: skip
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        glob = Path(env["GIT_CONFIG_GLOBAL"])
        assert glob.is_file() and glob.read_bytes() == b""


def test_git_ignores_user_insteadof(local_repo, git_fetcher, manifest, tmp_path, monkeypatch):
    """Q13: a url.*.insteadOf rule in the user's global config cannot redirect a fetch."""
    other = _other_repo(tmp_path, b"# Hijacked\n")
    cfg = tmp_path / "user.gitconfig"
    cfg.write_text(f'[url "{other.as_uri()}"]\n\tinsteadOf = {local_repo.as_uri()}\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    docs = list(git_fetcher.fetch(git_src(local_repo, paths=["docs"]), TECH, manifest))
    assert [d.body for d in docs] == [b"# Guide\n\nline\n"]
