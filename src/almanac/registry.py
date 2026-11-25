from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from almanac.errors import ConfigError
from almanac.models import Config


def _plain(node: Any) -> Any:
    if isinstance(node, dict):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, str):
        return str(node)
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    return node


def _field_name(loc: tuple) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "<root>"


def _line_for(tree: Any, loc: tuple) -> int:
    """1-based line of the deepest node along ``loc`` that exists in the YAML tree."""
    line = 1
    node = tree
    for part in loc:
        try:
            if isinstance(part, int) and isinstance(node, list):
                line = node.lc.item(part)[0] + 1
                node = node[part]
            elif isinstance(node, dict) and part in node:
                line = node.lc.key(part)[0] + 1
                node = node[part]
            else:
                break
        except (AttributeError, IndexError, KeyError, TypeError):
            break
    return line


def load_config(path: Path) -> Config:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(path, None, "<file>", f"cannot read config: {e.strerror or e}") from e
    try:
        tree = YAML(typ="rt").load(text)
    except YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        problem = getattr(e, "problem", None) or "invalid YAML"
        raise ConfigError(path, line, "<yaml>", str(problem)) from e
    if tree is None:
        tree = {}
    if not isinstance(tree, dict):
        raise ConfigError(path, 1, "<root>", "top level must be a mapping")

    try:
        cfg = Config.model_validate(_plain(tree))
    except ValidationError as e:
        err = e.errors()[0]
        loc = tuple(err["loc"])
        ctx = err.get("ctx") or {}
        if err["type"] == "missing_field" and "field" in ctx:
            loc = (*loc, ctx["field"])
        raise ConfigError(path, _line_for(tree, loc), _field_name(loc), err["msg"]) from e

    seen_tech: set[str] = set()
    seen_src: set[str] = set()
    for ti, tech in enumerate(cfg.technologies):
        if tech.id in seen_tech:
            loc = ("technologies", ti, "id")
            raise ConfigError(path, _line_for(tree, loc), _field_name(loc), f"duplicate technology id '{tech.id}'")
        seen_tech.add(tech.id)
        for si, src in enumerate(tech.sources):
            if src.id in seen_src:
                loc = ("technologies", ti, "sources", si, "id")
                raise ConfigError(path, _line_for(tree, loc), _field_name(loc), f"duplicate source id '{src.id}'")
            seen_src.add(src.id)

    cfg.root = path.parent.resolve()
    return cfg
