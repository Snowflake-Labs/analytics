from __future__ import annotations

from typing import Any, TypedDict


class AttemptResult(TypedDict):
    status: str
    client_elapsed_s: float
    row_count: int
    query_id: str | None
    columns: list[str]
    rows: list[tuple[Any, ...]]
    error: str | None


class QueryResult(TypedDict, total=False):
    query: str
    iteration: int
    status: str
    row_count: int
    client_elapsed_s: float
    query_id: str | None
    attempt_query_ids: list[str | None]
    attempt_client_times: list[float]
    result_columns: list[str]
    result_rows: list[tuple[Any, ...]]
    server_elapsed_s: float | None
    attempt_server_times: list[float]
    error: str | None
    validation: str
    validation_error: str | None
