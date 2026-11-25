from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


class RobotsCache:
    """robots.txt per scheme+host. 4xx allows all; 5xx or network error disallows all (RFC 9309)."""

    def __init__(self, fetch_text: Callable[[str], tuple[int, str]], user_agent: str):
        self._fetch = fetch_text
        self._ua = user_agent
        self._rules: dict[tuple[str, str], RobotFileParser | bool] = {}

    def _load(self, scheme: str, host: str) -> RobotFileParser | bool:
        key = (scheme, host)
        if key not in self._rules:
            try:
                status, text = self._fetch(f"{scheme}://{host}/robots.txt")
            except Exception:  # noqa: BLE001 - any failure to get robots.txt means disallow all
                self._rules[key] = False
            else:
                if 200 <= status < 300:
                    parser = RobotFileParser()
                    parser.parse(text.splitlines())
                    self._rules[key] = parser
                else:
                    self._rules[key] = status < 500
        return self._rules[key]

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        rules = self._load(parts.scheme, parts.netloc)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(self._ua, url)

    def crawl_delay(self, host: str) -> float | None:
        for (_, netloc), rules in self._rules.items():
            if netloc == host and isinstance(rules, RobotFileParser):
                delay = rules.crawl_delay(self._ua)
                if delay is not None:
                    return float(delay)
        return None
