from __future__ import annotations

import time

from bench_core.types import AttemptResult, QueryResult


def _make_query_result(
    *,
    status: str,
    elapsed: float,
    row_count: int,
    query_id: str | None,
    columns: list[str] | None = None,
    rows: list[tuple] | None = None,
    error: str | None = None,
) -> AttemptResult:
    return {
        "status": status,
        "client_elapsed_s": elapsed,
        "row_count": row_count,
        "query_id": query_id,
        "columns": columns or [],
        "rows": rows or [],
        "error": error,
    }


def run_query(cur, sql: str) -> AttemptResult:
    """Execute a single query and return timing + result metadata."""
    start = time.perf_counter()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description] if cur.description else []
        return _make_query_result(
            status="OK",
            elapsed=time.perf_counter() - start,
            row_count=len(rows),
            query_id=cur.sfqid,
            columns=columns,
            rows=rows,
        )
    except Exception as exc:  # noqa: BLE001
        return _make_query_result(
            status="FAILED",
            elapsed=time.perf_counter() - start,
            row_count=0,
            query_id=getattr(cur, "sfqid", None),
            error=str(exc),
        )


def run_attempts(cur, sql: str, repeats: int) -> list[AttemptResult]:
    """Run up to `repeats` attempts, stopping early on failure."""
    attempts: list[AttemptResult] = []
    for _ in range(repeats):
        attempt = run_query(cur, sql)
        attempts.append(attempt)
        if attempt["status"] != "OK":
            break
    return attempts


def best_result(name: str, iteration: int, attempts: list[AttemptResult]) -> QueryResult:
    """Pick the best (min client time) attempt and build a per-query result row."""
    ok_attempts = [a for a in attempts if a["status"] == "OK"]
    if ok_attempts:
        best = min(ok_attempts, key=lambda a: a["client_elapsed_s"])
        return {
            "query": name,
            "iteration": iteration,
            "status": "OK",
            "row_count": best["row_count"],
            "client_elapsed_s": best["client_elapsed_s"],
            "query_id": best["query_id"],
            "attempt_query_ids": [a["query_id"] for a in ok_attempts],
            "attempt_client_times": [a["client_elapsed_s"] for a in ok_attempts],
            "result_columns": best["columns"],
            "result_rows": best["rows"],
            "error": None,
        }
    last = attempts[-1]
    return {
        "query": name,
        "iteration": iteration,
        "status": "FAILED",
        "row_count": 0,
        "client_elapsed_s": last["client_elapsed_s"],
        "query_id": last["query_id"],
        "attempt_query_ids": [a["query_id"] for a in attempts if a["query_id"]],
        "attempt_client_times": [a["client_elapsed_s"] for a in attempts],
        "error": last["error"],
    }


def avg_result(name: str, iteration: int, attempts: list[AttemptResult]) -> QueryResult:
    """Compute average client time across successful attempts."""
    ok_attempts = [a for a in attempts if a["status"] == "OK"]
    if ok_attempts:
        import statistics

        avg_elapsed = statistics.fmean(a["client_elapsed_s"] for a in ok_attempts)
        last_ok = ok_attempts[-1]
        return {
            "query": name,
            "iteration": iteration,
            "status": "OK",
            "row_count": last_ok["row_count"],
            "client_elapsed_s": avg_elapsed,
            "query_id": last_ok["query_id"],
            "attempt_query_ids": [a["query_id"] for a in ok_attempts],
            "attempt_client_times": [a["client_elapsed_s"] for a in ok_attempts],
            "result_columns": last_ok["columns"],
            "result_rows": last_ok["rows"],
            "error": None,
        }
    last = attempts[-1]
    return {
        "query": name,
        "iteration": iteration,
        "status": "FAILED",
        "row_count": 0,
        "client_elapsed_s": last["client_elapsed_s"],
        "query_id": last["query_id"],
        "attempt_query_ids": [a["query_id"] for a in attempts if a["query_id"]],
        "attempt_client_times": [a["client_elapsed_s"] for a in attempts],
        "error": last["error"],
    }


def print_query_finished(result: QueryResult) -> None:
    if result["status"] == "OK":
        print(f" OK ({result['client_elapsed_s']:.3f}s)")
    else:
        print(" FAIL")
        if result.get("error"):
            print(f"    error: {result['error'][:200]}")


def run_benchmark_iteration(
    queries: list[tuple[str, str]],
    *,
    iteration: int,
    repeats: int,
    cur,
    use_avg: bool = False,
) -> list[QueryResult]:
    results: list[QueryResult] = []
    for name, sql in queries:
        if use_avg:
            print(f"  running {name} (avg of {repeats}, +1 warmup)…", end="", flush=True)
            # Warmup run — discard result
            run_query(cur, sql)
            # Measured runs
            attempts = run_attempts(cur, sql, repeats)
            result = avg_result(name, iteration, attempts)
        else:
            print(f"  running {name} (best of {repeats})…", end="", flush=True)
            attempts = run_attempts(cur, sql, repeats)
            result = best_result(name, iteration, attempts)
        print_query_finished(result)
        results.append(result)
    return results
