"""Write the run to SNOWFLAKE.INTERACTIVE_ANALYTICS through WRITE_BENCHMARK_REPORT, for Snowsight.

The procedure replaces everything stored for a benchmark_id on each call, and only the user who
created a benchmark may update it, so the controller is its only writer (see the sandbox README).
"""

from __future__ import annotations

import heapq
import json
import os
from itertools import chain
from typing import Any

from snowflake.connector.errors import ProgrammingError

from iwb import metrics
from iwb.config import Config, ConfigError
from iwb.snow import Snow
from iwb.warehouse import Warehouse, planned
from iwb.workload import Query, declared, use_warehouse

# Overridable to test against a copy of the procedure on an account without the feature.
PROCEDURE = os.environ.get("IWB_REPORT_PROCEDURE", "SNOWFLAKE.INTERACTIVE_ANALYTICS.WRITE_BENCHMARK_REPORT")
REPORT_VERSION = 1
MAX_EXECUTIONS = 25  # the procedure's per-test limit; the slowest are kept
# What a role without the writer application role gets for the procedure or its schema.
NOT_FOUND, UNKNOWN_FUNCTION = 2003, 2141


def weights_percent(weights: list[float]) -> list[float]:
    """Round to the procedure's NUMBER(5,2) so the values still sum to exactly 100."""
    total = sum(weights)
    cents = [w * 10000 / total for w in weights]
    rounded = [int(c) for c in cents]
    by_remainder = sorted(range(len(cents)), key=lambda i: cents[i] - rounded[i], reverse=True)
    for i in by_remainder[:10000 - sum(rounded)]:
        rounded[i] += 1
    return [r / 100 for r in rounded]


def query_hashes(snow: Snow, warm_ids: dict[str, str]) -> dict[str, str]:
    """QUERY_HASH -> workload query id, from this session's warm-up statements. A hash shared by two
    queries (the same text in different contexts) is left out, so those get no per-query rows."""
    rows = snow.rows("SELECT QUERY_ID, QUERY_HASH FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION("
                     f"RESULT_LIMIT => {metrics.RESULT_LIMIT}))")
    owners: dict[str, set[str]] = {}
    for row in rows:
        if row["QUERY_ID"] in warm_ids and row["QUERY_HASH"]:
            owners.setdefault(row["QUERY_HASH"], set()).add(warm_ids[row["QUERY_ID"]])
    return {h: next(iter(ids)) for h, ids in owners.items() if len(ids) == 1}


def _history_id(q: Query) -> str | None:
    return q.source if q.from_history else None


def _metric(rows: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    latency = metrics.latency([r["TOTAL_ELAPSED_TIME"] for r in rows])
    return {
        "sample_count": len(rows),
        "successful_count": sum(r["EXECUTION_STATUS"] == "SUCCESS" for r in rows),
        **{key: latency[key] for key in ("p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms", "avg_ms")},
        "throughput_qps": len(rows) / seconds if seconds > 0 else None,
    }


def _execution(row: dict[str, Any], query_id: str, number: int) -> dict[str, Any]:
    return {
        "execution_id": row["QUERY_ID"],
        "query_id": query_id,
        "invocation_number": number,
        "status": "SUCCESS" if row["EXECUTION_STATUS"] == "SUCCESS" else "FAILED",
        "started_at": row["START_TIME"],
        "completed_at": row["END_TIME"],
        "total_ms": row["TOTAL_ELAPSED_TIME"],
        "compile_ms": row["COMPILATION_TIME"],
        "queue_ms": row["QUEUED_MS"],
        "execute_ms": row["EXECUTION_TIME"],
        "bytes_scanned": row["BYTES_SCANNED"],
        "rows_returned": row["ROWS_PRODUCED"],
        "error_code": row["ERROR_CODE"],
        "error_message": row["ERROR_MESSAGE"],
    }


class Reporter:
    def __init__(self, cfg: Config, run_id: str, started_at: str) -> None:
        assert cfg.report is not None
        self.cfg = cfg
        self.benchmark_id = cfg.report.benchmark_id or run_id
        self.run_id = run_id
        self.test_id = f"{run_id}_T1"
        self.queries = declared(cfg)  # replaced by the resolved workload
        self.started_at = started_at
        self.hashes: dict[str, str] = {}
        self.metrics: list[dict[str, Any]] = []
        self.executions: list[dict[str, Any]] = []

    def measure(self, statements: list[dict[str, Any]], hashes: dict[str, str], seconds: float) -> None:
        """Keep the workload and per-query metrics and the slowest statements, not every row."""
        report_ids = {q.id: _history_id(q) or q.id for q in self.queries}
        self.hashes = {qid: h for h, qid in hashes.items()}
        by_query: dict[str, list[dict[str, Any]]] = {}
        for row in statements:
            if row["QUERY_HASH"] in hashes:
                by_query.setdefault(hashes[row["QUERY_HASH"]], []).append(row)
        self.metrics = [{"metric_id": f"{self.test_id}:ALL", "query_id": None, **_metric(statements, seconds)}]
        self.metrics += [{"metric_id": f"{self.test_id}:{qid}", "query_id": report_ids[qid], **_metric(rows, seconds)}
                         for qid, rows in sorted(by_query.items())]
        slowest = heapq.nlargest(MAX_EXECUTIONS, chain.from_iterable(by_query.values()),
                                 key=lambda r: r["TOTAL_ELAPSED_TIME"])
        self.executions = [_execution(r, report_ids[hashes[r["QUERY_HASH"]]], i) for i, r in enumerate(slowest, 1)]

    def _queries(self) -> list[dict[str, Any]]:
        """One row per report query id: the procedure needs them unique, and a query ID listed twice
        is only merged (by text) once it is resolved."""
        merged: dict[str, tuple[Query, float]] = {}
        for q in self.queries:
            report_id = _history_id(q) or q.id
            first, weight = merged.get(report_id, (q, 0))
            merged[report_id] = (first, weight + q.weight)
        weights = weights_percent([weight for _, weight in merged.values()])
        return [{
            "query_id": report_id,
            "query_order": i,
            "query_text": q.sql,
            "query_hash": self.hashes.get(q.id),
            "query_name": q.id,
            "weight_percent": weight,
            "source_type": "QUERY_HISTORY" if q.from_history else "MANUAL",
            "source_reference": _history_id(q),
        } for i, ((report_id, (q, _)), weight) in enumerate(zip(merged.items(), weights, strict=True), 1)]

    def payload(self, status: str, result: str | None = None, wh: Warehouse | None = None,
                completed_at: str | None = None, error_code: str | None = None,
                error_message: str | None = None) -> dict[str, Any]:
        cfg, new = self.cfg, self.cfg.new_warehouse
        if wh is None:
            # Before SETUP_WAREHOUSE: the planned warehouse, which a run that ends here never created.
            wh = planned(cfg, self.run_id)
            wh.created = wh.created and status in ("CREATED", "RUNNING")
        test = {
            "test_id": self.test_id,
            "attempt_number": 1,
            "warehouse_name": wh.name,
            "warehouse_size": wh.size,
            "min_cluster_count": new.min_cluster_count if new else None,
            "max_cluster_count": new.max_cluster_count if new else None,
            "concurrency": cfg.concurrent_users,
            "runtime_seconds": cfg.run_seconds,
            "warehouse_created_for_run": wh.created,
            "delete_warehouse_after_run": wh.created,
            "status": status,
            "started_at": self.started_at,
            "completed_at": completed_at,
            "metrics": self.metrics,
            "executions": self.executions,
        }
        return {
            "report_version": REPORT_VERSION,
            "benchmark_id": self.benchmark_id,
            "parent_benchmark_id": cfg.report.parent_benchmark_id,
            "name": cfg.report.name,
            "tags": cfg.report.tags,
            "description": cfg.report.description,
            "status": status,
            "result": result,
            "requested_concurrency": cfg.concurrent_users,
            "runtime_seconds": cfg.run_seconds,
            "started_at": self.started_at,
            "completed_at": completed_at,
            "error_code": error_code,
            "error_message": error_message,
            "queries": self._queries(),
            "tests": [test],
        }

    def write(self, snow: Snow, payload: dict[str, Any]) -> None:
        """Interactive warehouses cannot call procedures, so this switches to report.warehouse."""
        try:
            use_warehouse(snow, self.cfg.report.warehouse)
            snow.execute(f"CALL {PROCEDURE}(PARSE_JSON(%s))", (json.dumps(payload, default=str),))
        except ProgrammingError as exc:
            hint = (" Grant SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE to this role."
                    if exc.errno in (NOT_FOUND, UNKNOWN_FUNCTION) else "")
            raise ConfigError(f"Cannot write the benchmark report with {PROCEDURE}: {exc.msg}{hint}") from exc
