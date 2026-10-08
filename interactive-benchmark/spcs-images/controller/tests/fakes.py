import re
from typing import Any


class FakeSnow:
    """Answers SQL by the first matching regex; records every statement."""

    def __init__(self) -> None:
        self.handlers: list[tuple[re.Pattern, Any]] = []
        self.statements: list[tuple[str, tuple | None]] = []

    def on(self, pattern: str, result: Any) -> "FakeSnow":
        self.handlers.append((re.compile(pattern, re.I | re.S), result))
        return self

    def _answer(self, sql: str, params: tuple | None) -> list[dict[str, Any]]:
        self.statements.append((sql, params))
        for pattern, result in self.handlers:
            if pattern.search(sql):
                if isinstance(result, Exception):
                    raise result
                if callable(result):
                    return result(sql, params)
                return result
        return []

    def rows(self, sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
        return self._answer(sql, params)

    def execute(self, sql: str, params: tuple | None = None) -> str:
        self._answer(sql, params)
        return "qid"

    def close(self) -> None:
        pass

    def ran(self, pattern: str) -> list[str]:
        return [sql for sql, _ in self.statements if re.search(pattern, sql, re.I | re.S)]
