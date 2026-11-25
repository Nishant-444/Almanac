from __future__ import annotations

from pathlib import Path


class AlmanacError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ConfigError(AlmanacError):
    def __init__(self, file: Path, line: int | None, field: str, message: str) -> None:
        super().__init__("E_BAD_INPUT", message, {"file": str(file), "line": line, "field": field})
        self.file = file
        self.line = line
        self.field = field

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.field}: {self.message}"


class FetchBlocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FetchFailed(Exception):
    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
