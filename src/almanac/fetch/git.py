from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from almanac.errors import FetchBlocked, FetchFailed
from almanac.fetch.fetchlog import FetchLog
from almanac.fetch.safety import check_url
from almanac.models import RawDoc, Settings, Source, Technology
from almanac.store import Manifest

_CONTENT_TYPES = {
    ".md": "text/markdown",
    ".mdx": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".rst": "text/plain",
}
_HARDENING = ("-c", "core.autocrlf=false", "-c", "http.followRedirects=false", "-c", "core.longpaths=true")
_GITHUB = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


def citation_url(url: str, sha: str, path: str) -> str:
    m = _GITHUB.match(url)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}/blob/{sha}/{path}"
    return f"{url.rstrip('/')}/{path}"


def stable_url(url: str, path: str) -> str:
    """A commit-independent URL for a repo file: the page identity key (no sha in it)."""
    m = _GITHUB.match(url)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}/{path}"
    return f"{url.rstrip('/')}/{path}"


def _loggable(url: str) -> str:
    """The URL with any userinfo removed, so credentials never reach the fetch log."""
    try:
        parts = urlsplit(url)
        if "://" in url and (parts.username is not None or parts.password is not None):
            host = parts.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            if parts.port:
                host += f":{parts.port}"
            return parts._replace(netloc=host).geturl()
    except ValueError:
        return "<invalid-url>"
    return url


def _force_remove(func, path, *_exc) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


class GitFetcher:
    def __init__(self, settings: Settings, cache_dir: Path, log: FetchLog, run_git: Callable = subprocess.run) -> None:
        self._settings = settings
        self._cache_dir = cache_dir
        self._log = log
        self._run = run_git

    def fetch(self, source: Source, tech: Technology, manifest: Manifest, force: bool = False) -> Iterator[RawDoc]:
        url = source.url or ""
        start = time.monotonic()
        error: str | None = None
        try:
            self._check(url)
            clone = self._cache_dir / source.id
            self._check_clone_dir(clone)
            self._sync(source, url, clone)
            sha = self._git(["rev-parse", "HEAD"], clone).strip()
        except (FetchBlocked, FetchFailed) as e:
            error = e.reason
            raise
        finally:
            self._log.record(_loggable(url), None, 0, int((time.monotonic() - start) * 1000), error)
        if sha == manifest.source_state(source.id).get("commit_sha") and not force:
            manifest.touch_source(source.id, datetime.now(UTC))  # a successful re-check
            return
        root = clone.resolve()
        for rel in self._files(clone, root):
            yield RawDoc(
                source_id=source.id,
                tech_id=tech.id,
                url=(u := citation_url(url, sha, rel)),
                final_url=u,
                body=(root / rel).read_bytes(),
                content_type=_CONTENT_TYPES[Path(rel).suffix.lower()],
                fetched_at=datetime.now(UTC),
                extra={"commit_sha": sha, "repo_path": rel, "stable_url": stable_url(url, rel)},
            )
        manifest.set_source_state(source.id, commit_sha=sha)

    def _check(self, url: str) -> None:
        if url.startswith("-"):
            raise FetchBlocked("scheme")
        if "://" in url:
            try:
                parts = urlsplit(url)
                has_creds = parts.username is not None or parts.password is not None
            except ValueError as e:
                raise FetchBlocked("scheme") from e
            if has_creds:
                raise FetchBlocked("credentials")
        if url.startswith("https://"):
            check_url(url, self._settings.allow_private_network)
        elif "://" in url and not url.startswith("file:") or not self._settings.allow_private_network:
            raise FetchBlocked("scheme")

    def _check_clone_dir(self, clone: Path) -> None:
        """Refuse any clone dir that is not a strict descendant of the cache dir (nothing is deleted)."""
        base = self._cache_dir.resolve()
        target = clone.resolve()
        if target == base or not target.is_relative_to(base):
            raise FetchBlocked("bad-source-id")

    def _sync(self, source: Source, url: str, clone: Path) -> None:
        if (clone / ".git").is_dir():
            self._git(["remote", "set-url", "origin", url], clone)  # the source's url may have changed
            self._git(["fetch", "--depth", "1", "origin", source.branch or "HEAD"], clone)
            self._sparse(source, clone)
            self._git(["reset", "--hard", "FETCH_HEAD"], clone)
            self._git(["clean", "-fdx"], clone)
            return
        if clone.exists():
            _rmtree(clone)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--depth", "1", "--filter=blob:none", "--no-checkout"]
        if source.branch:
            args += ["--branch", source.branch]
        try:
            self._git([*args, "--", url, str(clone)], None)
            self._sparse(source, clone)
            self._git(["checkout"], clone)
        except FetchFailed:
            if clone.exists():
                _rmtree(clone)
            raise

    def _sparse(self, source: Source, clone: Path) -> None:
        if source.paths:
            anchored = ["/" + p.strip("/") for p in source.paths]
            self._git(["sparse-checkout", "set", "--no-cone", *anchored], clone)
        else:
            self._git(["sparse-checkout", "disable"], clone)

    def _git(self, args: list[str], cwd: Path | None) -> str:
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            # an empty global config: the user's url.*.insteadOf rules cannot redirect fetches
            "GIT_CONFIG_GLOBAL": str(self._empty_config()),
        }
        try:
            proc = self._run(
                ["git", *_HARDENING, *args],
                cwd=cwd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise FetchFailed(f"git: {stderr.splitlines()[0] if stderr else 'exit ' + str(e.returncode)}") from e
        except OSError as e:
            raise FetchFailed(f"git: {e}") from e
        return proc.stdout or ""

    def _empty_config(self) -> Path:
        # Source ids cannot contain '.', so this name never collides with a clone dir.
        path = self._cache_dir / "empty.gitconfig"
        if not path.is_file() or path.stat().st_size:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        return path

    @staticmethod
    def _files(clone: Path, root: Path) -> Iterator[str]:
        for dirpath, dirnames, filenames in os.walk(clone):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in _CONTENT_TYPES:
                    continue
                p = Path(dirpath) / name
                if p.is_symlink() or not p.resolve().is_relative_to(root):
                    continue
                yield p.relative_to(clone).as_posix()
