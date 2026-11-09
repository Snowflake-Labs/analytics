from __future__ import annotations

import csv
import json
import statistics
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from src.ssb.config import RESULTS_DIR
from src.ssb.types import QueryResult


def fetch_server_elapsed(conn, query_ids: Iterable[str]) -> dict[str, float]:
    """Look up server-side total_elapsed_time for the executed queries."""
    ids = [q for q in query_ids if q]
    if not ids:
        return {}
    in_list = ", ".join(f"'{q}'" for q in ids)
    sql = (
        "SELECT QUERY_ID, TOTAL_ELAPSED_TIME "
        "FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION(RESULT_LIMIT=>1000)) "
        f"WHERE QUERY_ID IN ({in_list})"
    )
    out: dict[str, float] = {}
    with conn.cursor() as cur:
        cur.execute(sql)
        for qid, elapsed_ms in cur.fetchall():
            out[qid] = elapsed_ms / 1000.0
    return out


def enrich_server_elapsed(conn, results: list[QueryResult], *, use_avg: bool = False) -> None:
    """Attach server_elapsed_s to each result (best or avg attempt server time)."""
    all_ids = [qid for r in results for qid in r.get("attempt_query_ids", [])]
    sf_elapsed = fetch_server_elapsed(conn, all_ids)
    for r in results:
        servers = [sf_elapsed[q] for q in r.get("attempt_query_ids", []) if q in sf_elapsed]
        if not servers:
            r["server_elapsed_s"] = None
        elif use_avg:
            r["server_elapsed_s"] = sum(servers) / len(servers)
        else:
            r["server_elapsed_s"] = min(servers)
        r["attempt_server_times"] = servers


def _stats(times: list[float]) -> dict:
    if not times:
        return {k: 0.0 for k in ("total", "avg", "median", "min", "max", "p95")}
    return {
        "total": sum(times),
        "avg": statistics.fmean(times),
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "p95": statistics.quantiles(times, n=20)[18] if len(times) >= 20 else max(times),
    }


def summarize(results: list[QueryResult]) -> dict:
    ok = [r for r in results if r["status"] == "OK"]
    failed = [r for r in results if r["status"] != "OK"]
    validation_failed = [r for r in ok if r.get("validation") == "FAIL"]

    unique_queries = set(r["query"] for r in results)
    single_query = len(unique_queries) == 1

    if single_query:
        client_times = [t for r in ok for t in r.get("attempt_client_times", [r["client_elapsed_s"]])]
        server_times = [t for r in ok for t in r.get("attempt_server_times", [])]
        if not server_times:
            server_times = client_times
    else:
        client_times = [r["client_elapsed_s"] for r in ok]
        server_times = [
            r["server_elapsed_s"] if r.get("server_elapsed_s") is not None else r["client_elapsed_s"]
            for r in ok
        ]
    out = {
        "total_queries": len(results),
        "successful": len(ok),
        "failed": len(failed),
        "client_elapsed_s": _stats(client_times),
        "server_elapsed_s": _stats(server_times),
    }
    if any("validation" in r for r in results):
        out["validation_passed"] = sum(1 for r in ok if r.get("validation") == "PASS")
        out["validation_failed"] = len(validation_failed)
    return out


def _format_result_row(r: QueryResult, *, show_validation: bool) -> str:
    server_s = f"{(r.get('server_elapsed_s') or 0):>10.3f}"
    query_id = r.get("query_id") or ""
    if show_validation:
        validation = r.get("validation", "")
        return (
            f"{r['query']:10}  {r['status']:7}  {validation:5}  {r['row_count']:>8}  "
            f"{r['client_elapsed_s']:>10.3f}  {server_s}  {query_id}"
        )
    return (
        f"{r['query']:10}  {r['status']:7}  {r['row_count']:>8}  "
        f"{r['client_elapsed_s']:>10.3f}  {server_s}  {query_id}"
    )


def print_table(results: list[QueryResult]) -> None:
    show_validation = any("validation" in r for r in results)
    print("\nPer-query results:")
    if show_validation:
        print(
            f"{'query':10}  {'status':7}  {'valid':5}  {'rows':>8}  "
            f"{'client_s':>10}  {'server_s':>10}  query_id"
        )
        print("-" * 98)
    else:
        print(
            f"{'query':10}  {'status':7}  {'rows':>8}  "
            f"{'client_s':>10}  {'server_s':>10}  query_id"
        )
        print("-" * 90)
    for r in results:
        print(_format_result_row(r, show_validation=show_validation))
        if show_validation and r.get("validation") == "FAIL" and r.get("validation_error"):
            print(f"             validation: {r['validation_error']}")


def print_summary(summary: dict) -> None:
    print("\nSummary:")
    if summary.get("warehouse"):
        wh_size = summary.get("warehouse_size", "unknown")
        print(f"  warehouse     : {summary['warehouse']} ({wh_size})")
    print(f"  total_queries : {summary['total_queries']}")
    print(f"  successful    : {summary['successful']}")
    print(f"  failed        : {summary['failed']}")
    if "validation_passed" in summary:
        print(f"  validated OK  : {summary['validation_passed']}")
        print(f"  validated FAIL: {summary['validation_failed']}")

    client = summary["client_elapsed_s"]
    server = summary["server_elapsed_s"]
    print(f"\n  {'metric':10}  {'client_s':>10}  {'server_s':>10}")
    print("  " + "-" * 34)
    for key in ("total", "avg", "median", "min", "max", "p95"):
        print(f"  {key:10}  {client[key]:>10.3f}  {server[key]:>10.3f}")


def _serialize_result(result: QueryResult) -> dict:
    out = {k: v for k, v in result.items() if k not in ("result_columns", "result_rows")}
    return out


def write_results(
    warehouse_type: str, tables_type: str, scale: str, workload: str,
    results: list[QueryResult], summary: dict,
) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"run_{warehouse_type}-{tables_type}_sf{scale}_{workload}_{ts}"
    json_path = RESULTS_DIR / f"{base}.json"
    csv_path = RESULTS_DIR / f"{base}.csv"

    json_path.write_text(
        json.dumps(
            {
                "warehouse_type": warehouse_type,
                "tables_type": tables_type,
                "scale": scale,
                "workload": workload,
                "summary": summary,
                "results": [_serialize_result(r) for r in results],
            },
            indent=2,
        )
    )

    fieldnames = [
        "query",
        "iteration",
        "status",
        "validation",
        "row_count",
        "client_elapsed_s",
        "server_elapsed_s",
        "query_id",
        "error",
        "validation_error",
        "server_version",
    ]
    server_version = summary.get("server_version", "")
    with csv_path.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            row = {k: r.get(k) for k in fieldnames if k != "server_version"}
            row["server_version"] = server_version
            w.writerow(row)

    return json_path, csv_path
