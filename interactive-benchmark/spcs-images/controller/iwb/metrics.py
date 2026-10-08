"""Server-side latency for the run window, from INFORMATION_SCHEMA.QUERY_HISTORY_BY_WAREHOUSE."""

from __future__ import annotations

import math
from typing import Any

from snowflake.connector.errors import DatabaseError

from iwb.snow import STATEMENT_TIMEOUT, Snow
from iwb.workload import ident

SLICE_SECONDS = 60
RESULT_LIMIT = 10000

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


def _fetch(snow: Snow, database: str, warehouse: str, start: int, end: int) -> list[dict[str, Any]] | None:
    """History rows ending in [start, end), or None if the result may be incomplete.

    RESULT_LIMIT applies before the QUERY_TAG filter, so a full result means rows were dropped. The
    session is on the interactive warehouse, so a large slice can also hit its statement timeout.
    """
    try:
        rows = snow.rows(
            f"SELECT {COLUMNS}, QUERY_TAG FROM TABLE({ident(database)}.INFORMATION_SCHEMA."
            "QUERY_HISTORY_BY_WAREHOUSE(WAREHOUSE_NAME => %s, END_TIME_RANGE_START => TO_TIMESTAMP_LTZ(%s), "
            "END_TIME_RANGE_END => TO_TIMESTAMP_LTZ(%s), RESULT_LIMIT => %s))",
            (warehouse, start, end, RESULT_LIMIT),
        )
    except DatabaseError as exc:
        if exc.errno != STATEMENT_TIMEOUT:
            raise
        return None
    return rows if len(rows) < RESULT_LIMIT else None


def _collect(snow: Snow, database: str, warehouse: str, tag: str, start: float, end: float) -> list[dict[str, Any]]:
    """Read the window in slices, halving the slice length (for the rest of the window too) while a
    slice comes back incomplete."""
    rows: list[dict[str, Any]] = []
    step = SLICE_SECONDS
    t, stop = math.floor(start), math.ceil(end)
    while t < stop:
        slice_end = min(t + step, stop)
        chunk = _fetch(snow, database, warehouse, t, slice_end)
        if chunk is None:
            if slice_end - t <= 1:
                raise RuntimeError(
                    f"More than {RESULT_LIMIT} queries ended on {warehouse} within one second at {t}, or that "
                    "second's history hit the statement timeout; server-side metrics would be incomplete"
                )
            step = max(1, (slice_end - t) // 2)
            continue
        rows.extend(r for r in chunk if r["QUERY_TAG"] == tag)
        t = slice_end
    return rows


def server_side(snow: Snow, database: str, warehouse: str, fallback: str | None, tag: str,
                start: float, end: float) -> dict[str, Any]:
    rows = _collect(snow, database, warehouse, tag, start, end)
    if fallback:
        rows += _collect(snow, database, fallback, tag, start, end)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        prev = latest.get(row["QUERY_ID"])
        if prev is None or row["END_TIME"] > prev["END_TIME"]:
            latest[row["QUERY_ID"]] = row
    # Slices are whole seconds; keep only statements that ended inside the exact Locust window.
    queries = [r for r in latest.values() if start <= r["END_TIME"].timestamp() < end]
    elapsed = [r["TOTAL_ELAPSED_TIME"] for r in queries]
    succeeded = [r for r in queries if r["EXECUTION_STATUS"] == "SUCCESS"]
    on_interactive = [r for r in succeeded if r["WAREHOUSE_NAME"].upper() == warehouse.upper()]

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
