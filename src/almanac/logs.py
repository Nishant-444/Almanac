"""JSON-lines logging. stdout is reserved for the MCP protocol, so no handler here ever writes to it."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from almanac.paths import AlmanacPaths

_MARK = "_almanac_handler"


class JsonFormatter(logging.Formatter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "run_id": self.run_id,
            "module": record.name,
            "msg": record.getMessage(),
        }
        duration = getattr(record, "duration_ms", None)
        if duration is not None:
            entry["duration_ms"] = duration
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure(paths: AlmanacPaths, run_id: str, level: str = "INFO", stderr: bool = False) -> logging.Logger:
    """Attach a JSONL file handler (and optionally stderr) to the ``almanac`` logger. Idempotent."""
    log = logging.getLogger("almanac")
    for h in list(log.handlers):
        if getattr(h, _MARK, False):
            log.removeHandler(h)
            h.close()
    paths.logs.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter(run_id)
    handlers: list[logging.Handler] = [logging.FileHandler(Path(paths.log_file), encoding="utf-8")]
    if stderr:
        handlers.append(logging.StreamHandler())  # defaults to sys.stderr
    for h in handlers:
        h.setFormatter(formatter)
        setattr(h, _MARK, True)
        log.addHandler(h)
    log.setLevel(level.upper())
    log.propagate = False
    return log
