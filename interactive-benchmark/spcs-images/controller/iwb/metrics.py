"""Server-side latency for the run window, from INFORMATION_SCHEMA.QUERY_HISTORY_BY_WAREHOUSE."""

from __future__ import annotations

import math
from typing import Any

from snowflake.connector.errors import DatabaseError

from iwb.events import Events
from iwb.snow import Snow
from iwb.workload import ident

SLICE_SECONDS = 60
RESULT_LIMIT = 10000
INTERACTIVE_TIMEOUT_MS = 5000
STATEMENT_TIMEOUT = 630

COLUMNS = (
    "QUERY_ID, EXECUTION_STATUS, WAREHOUSE_NAME, CLUSTER_NUMBER, END_TIME, TOTAL_ELAPSED_TIME, "
    "COMPILATION_TIME, EXECUTION_TIME, QUEUED_PROVISIONING_TIME + QUEUED_OVERLOAD_TIME AS QUEUED_MS"
)


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _slice(snow: Snow, database: str, warehouse: str, tag: str, start: int, end: int) -> list[dict[str, Any]]:
    """History rows ending in [start, end); halves the range while the result may be truncated.

    RESULT_LIMIT applies before the QUERY_TAG filter, so a full result means rows were dropped. The
    session is on the interactive warehouse, so a large slice can also hit its statement timeout.
    """
    def split() -> list[dict[str, Any]]:
        mid = (start + end) // 2
        return _slice(snow, database, warehouse, tag, start, mid) + _slice(snow, database, warehouse, tag, mid, end)

    try:
        rows = snow.rows(
            f"SELECT {COLUMNS}, QUERY_TAG FROM TABLE({ident(database)}.INFORMATION_SCHEMA."
            "QUERY_HISTORY_BY_WAREHOUSE(WAREHOUSE_NAME => %s, END_TIME_RANGE_START => TO_TIMESTAMP_LTZ(%s), "
            "END_TIME_RANGE_END => TO_TIMESTAMP_LTZ(%s), RESULT_LIMIT => %s))",
            (warehouse, start, end, RESULT_LIMIT),
        )
    except DatabaseError as exc:
        if exc.errno != STATEMENT_TIMEOUT or end - start <= 1:
            raise
        return split()
    if len(rows) >= RESULT_LIMIT:
        if end - start <= 1:
            raise RuntimeError(
                f"More than {RESULT_LIMIT} queries ended on {warehouse} within one second at {start}; "
                "server-side metrics would be incomplete"
            )
        return split()
    return [r for r in rows if r["QUERY_TAG"] == tag]


def _collect(snow: Snow, database: str, warehouse: str, tag: str, start: float, end: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    t = math.floor(start)
    while t < end:
        slice_end = min(t + SLICE_SECONDS, math.ceil(end))
        rows.extend(_slice(snow, database, warehouse, tag, t, slice_end))
        t = slice_end
    return rows


def server_side(snow: Snow, database: str, warehouse: str, fallback: str | None, tag: str,
                start: float, end: float, events: Events) -> dict[str, Any]:
    rows = _collect(snow, database, warehouse, tag, start, end)
    if fallback:
        rows += _collect(snow, database, fallback, tag, start, end)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        prev = latest.get(row["QUERY_ID"])
        if prev is None or row["END_TIME"] > prev["END_TIME"]:
            latest[row["QUERY_ID"]] = row
    queries = list(latest.values())
    elapsed = [r["TOTAL_ELAPSED_TIME"] for r in queries]
    succeeded = [r for r in queries if r["EXECUTION_STATUS"] == "SUCCESS"]
    on_interactive = [r for r in succeeded if r["TOTAL_ELAPSED_TIME"] <= INTERACTIVE_TIMEOUT_MS
                      and r["WAREHOUSE_NAME"].upper() == warehouse.upper()]

    def avg(key: str) -> float | None:
        return sum(r[key] for r in queries) / len(queries) if queries else None

    return {
        "n": len(queries),
        "n_failed": len(queries) - len(succeeded),
        "n_fallback": len(succeeded) - len(on_interactive),
        "p50_ms": percentile(elapsed, 50),
        "p90_ms": percentile(elapsed, 90),
        "p95_ms": percentile(elapsed, 95),
        "p99_ms": percentile(elapsed, 99),
        "p95_interactive_only_ms": percentile([r["TOTAL_ELAPSED_TIME"] for r in on_interactive], 95),
        "avg_compile_ms": avg("COMPILATION_TIME"),
        "avg_exec_ms": avg("EXECUTION_TIME"),
        "avg_queue_ms": avg("QUEUED_MS"),
        "clusters_used": len({r["CLUSTER_NUMBER"] for r in queries
                              if r["WAREHOUSE_NAME"].upper() == warehouse.upper()}),
    }
