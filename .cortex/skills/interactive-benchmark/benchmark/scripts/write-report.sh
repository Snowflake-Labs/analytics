#!/usr/bin/env bash
# Write the benchmark to SNOWFLAKE.INTERACTIVE_ANALYTICS, where Snowsight shows it.
# Usage:
#   write-report.sh <report_dir> created [--warehouse-created] [--description TEXT]
#   write-report.sh <report_dir> running
#   write-report.sh <report_dir> iteration <N> <RUN_START> <RUN_END>   # after QH_RUN_<N> exists
#   write-report.sh <report_dir> final <COMPLETED|ERROR|CANCELLED> [SUCCESS|FAIL] [--message TEXT]
#     (COMPLETED defaults to SUCCESS; the skill passes FAIL when the P95 goal was not met)
#
# WRITE_BENCHMARK_REPORT replaces everything stored for the benchmark on each call, so every call
# sends the full state, kept in <report_dir>/benchmark-report-state.json. Each escalation iteration
# is one test. If ROLE cannot see the procedure, the report is skipped with a message.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

if (( $# < 2 )); then
  sed -n '3,8p' "$0" >&2
  exit 1
fi

python3 - "$@" <<'PYEOF'
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROCEDURE = os.environ.get("IWB_REPORT_PROCEDURE", "SNOWFLAKE.INTERACTIVE_ANALYTICS.WRITE_BENCHMARK_REPORT")
ROLE_TO_GRANT = "SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE"
MAX_EXECUTIONS = 25
env = os.environ


def fail(message: str) -> None:
    print(f"write-report.sh: {message}", file=sys.stderr)
    sys.exit(1)


def sql(statements: str) -> list[dict]:
    """Rows of the last statement; the script goes on stdin, since a report can reach 1 MB."""
    out = subprocess.run(["snow", "sql", "--connection", env["CONNECTION"], "--role", env["ROLE"], "--silent",
                          "--format", "json", "-i"], input=statements, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip())
    data = json.loads(out.stdout)
    return data[-1] if data and isinstance(data[0], list) else data


def procedure_visible() -> bool:
    schema, name = PROCEDURE.rsplit(".", 1)
    try:
        return bool(sql(f"SHOW PROCEDURES LIKE '{name}' IN SCHEMA {schema};"))
    except RuntimeError as exc:
        if "does not exist or not authorized" in str(exc):
            return False
        raise


def seconds(duration: str) -> int:
    """LOCUST_RUN_TIME (3m, 90s, 1h30m) in seconds."""
    parts = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", duration.strip())
    if not duration.strip() or not parts:
        fail(f"cannot read LOCUST_RUN_TIME {duration!r}")
    h, m, s = (int(p or 0) for p in parts.groups())
    return h * 3600 + m * 60 + s


def weights_percent(count: int) -> list[float]:
    """Equal weights rounded to the procedure's NUMBER(5,2), still summing to exactly 100."""
    base, extra = divmod(10000, count)
    return [(base + (1 if i < extra else 0)) / 100 for i in range(count)]


def queries(previous: list[dict]) -> list[dict]:
    """The benchmark .sql files, equally weighted. A file removed mid-run stays at weight 0, so earlier
    iterations' rows still reference a query in the report."""
    files = sorted(Path(env["BENCHMARK_QUERY_DIR"]).glob("*.sql"))
    texts = {f.stem: f.read_text().strip() for f in files}
    texts = {stem: text for stem, text in texts.items() if text}  # the API skips empty files too
    if not texts:
        fail(f"no benchmark .sql files in {env['BENCHMARK_QUERY_DIR']}")
    rows = [(stem, text, w) for (stem, text), w in zip(texts.items(), weights_percent(len(texts)), strict=True)]
    rows += [(q["query_id"], q["query_text"], 0.0) for q in previous if q["query_id"] not in texts]
    return [{"query_id": stem, "query_order": i, "query_text": text, "query_name": stem, "weight_percent": w,
             "source_type": "MANUAL"} for i, (stem, text, w) in enumerate(rows, 1)]


def size(shown: str | None) -> str | None:
    """SHOW WAREHOUSES' size ("X-Small", "2X-Large") as the benchmark controller reports it ("XSMALL")."""
    if not shown:
        return None
    spelled = shown.upper().replace("-", "")
    multiple = re.fullmatch(r"(\d)XLARGE", spelled)
    if multiple:
        n = int(multiple[1])
        return "X" * n + "LARGE" if n <= 3 else f"X{n}LARGE"
    return spelled


def iteration(state: dict, number: int, run_start: int, run_end: int) -> dict:
    """One test from QH_RUN_<N>, with the warehouse as it is now (before any further escalation)."""
    table = f"{env['DB']}.{env['SCHEMA']}.QH_RUN_{number}"
    latest = (f"WITH q AS (SELECT * FROM {table} "
              "QUALIFY ROW_NUMBER() OVER (PARTITION BY QUERY_ID ORDER BY END_TIME DESC) = 1)")
    wh = sql(f"SHOW WAREHOUSES LIKE '{env['INTERACTIVE_WAREHOUSE']}';")
    if not wh:
        fail(f"warehouse {env['INTERACTIVE_WAREHOUSE']} not found")
    groups = sql(f"""USE WAREHOUSE {env['DEPLOY_WAREHOUSE']};
{latest}
SELECT GROUPING(QUERY_HASH) AS ALL_ROWS, QUERY_HASH, ANY_VALUE(QUERY_TEXT) AS QUERY_TEXT, COUNT(*) AS N,
  COUNT_IF(EXECUTION_STATUS = 'SUCCESS') AS N_OK, MEDIAN(TOTAL_ELAPSED_TIME) AS P50,
  APPROX_PERCENTILE(TOTAL_ELAPSED_TIME, 0.95) AS P95, APPROX_PERCENTILE(TOTAL_ELAPSED_TIME, 0.99) AS P99,
  MIN(TOTAL_ELAPSED_TIME) AS MIN_MS, MAX(TOTAL_ELAPSED_TIME) AS MAX_MS, AVG(TOTAL_ELAPSED_TIME) AS AVG_MS
FROM q GROUP BY GROUPING SETS ((), (QUERY_HASH));""")
    by_text: dict[str, list[str]] = {}
    for q in state["queries"]:
        by_text.setdefault(q["query_text"], []).append(q["query_id"])
    # A hash maps to a query by its text; two files with the same text cannot be told apart.
    owner = {g["QUERY_HASH"]: by_text[g["QUERY_TEXT"].strip()][0] for g in groups
             if not g["ALL_ROWS"] and g["QUERY_HASH"] is not None
             and len(by_text.get((g["QUERY_TEXT"] or "").strip(), [])) == 1}
    unmatched = sum(g["N"] for g in groups if not g["ALL_ROWS"] and g["QUERY_HASH"] not in owner)
    if unmatched:
        print(f"write-report.sh: warning: {unmatched} statements in QH_RUN_{number} match no single benchmark "
              ".sql file by text; they count only in the workload row.", file=sys.stderr)
    test_id = f"{state['benchmark_id']}_T{number}"
    window = run_end - run_start

    def metric(g: dict, query_id: str | None) -> dict:
        return {"metric_id": f"{test_id}:{query_id or 'ALL'}", "query_id": query_id, "sample_count": g["N"],
                "successful_count": g["N_OK"], "p50_ms": g["P50"], "p95_ms": g["P95"], "p99_ms": g["P99"],
                "min_ms": g["MIN_MS"], "max_ms": g["MAX_MS"], "avg_ms": g["AVG_MS"],
                "throughput_qps": g["N"] / window if window > 0 else None}

    metrics = [metric(g, None if g["ALL_ROWS"] else owner[g["QUERY_HASH"]]) for g in groups
               if g["ALL_ROWS"] or g["QUERY_HASH"] in owner]
    slowest = [r for r in sql(f"""USE WAREHOUSE {env['DEPLOY_WAREHOUSE']};
{latest}
SELECT QUERY_ID, QUERY_HASH, EXECUTION_STATUS, START_TIME, END_TIME, TOTAL_ELAPSED_TIME, COMPILATION_TIME,
  EXECUTION_TIME, QUEUED_PROVISIONING_TIME + QUEUED_OVERLOAD_TIME AS QUEUED_MS, BYTES_SCANNED, ROWS_PRODUCED,
  ERROR_CODE, ERROR_MESSAGE
FROM q ORDER BY TOTAL_ELAPSED_TIME DESC LIMIT {MAX_EXECUTIONS * 4};""") if r["QUERY_HASH"] in owner][:MAX_EXECUTIONS]
    stamp = lambda epoch: datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    return {
        "test_id": test_id, "attempt_number": number, "warehouse_name": wh[0]["name"],
        "warehouse_size": size(wh[0].get("size")), "min_cluster_count": int(wh[0]["min_cluster_count"]),
        "max_cluster_count": int(wh[0]["max_cluster_count"]), "concurrency": int(env["LOCUST_USERS"]),
        "runtime_seconds": seconds(env["LOCUST_RUN_TIME"]),
        "warehouse_created_for_run": state["warehouse_created"],
        "delete_warehouse_after_run": state["warehouse_created"], "status": "COMPLETED",
        "started_at": stamp(run_start), "completed_at": stamp(run_end), "metrics": metrics,
        "executions": [{
            "execution_id": r["QUERY_ID"], "query_id": owner[r["QUERY_HASH"]], "invocation_number": i,
            "status": "SUCCESS" if r["EXECUTION_STATUS"] == "SUCCESS" else "FAILED",
            "started_at": r["START_TIME"], "completed_at": r["END_TIME"], "total_ms": r["TOTAL_ELAPSED_TIME"],
            "compile_ms": r["COMPILATION_TIME"], "queue_ms": r["QUEUED_MS"], "execute_ms": r["EXECUTION_TIME"],
            "bytes_scanned": r["BYTES_SCANNED"], "rows_returned": r["ROWS_PRODUCED"],
            "error_code": r["ERROR_CODE"], "error_message": r["ERROR_MESSAGE"],
        } for i, r in enumerate(slowest, 1)],
    }


def option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    i = args.index(name)
    if i + 1 >= len(args):
        fail(f"{name} needs a value")
    value = args[i + 1]
    del args[i:i + 2]
    return value


def main(argv: list[str]) -> None:
    report_dir, command, args = Path(argv[0]), argv[1], argv[2:]
    state_file = report_dir / "benchmark-report-state.json"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if command == "created":
        created = "--warehouse-created" in args
        args = [a for a in args if a != "--warehouse-created"]
        description = option(args, "--description")
        if args:
            fail(f"unexpected arguments: {args}")
        # A new id per run: a benchmark name reused for a later run must not replace this one.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        state = {"benchmark_id": f"{env['SOLUTION_NAME']}_{stamp}", "started_at": now,
                 "warehouse_created": created, "description": description, "skipped": False, "queries": [],
                 "tests": []}
        report_dir.mkdir(parents=True, exist_ok=True)
        if not procedure_visible():
            state["skipped"] = True
            state_file.write_text(json.dumps(state, indent=2))
            print(f"Skipping the Snowsight benchmark report: {env['ROLE']} cannot see {PROCEDURE}. "
                  f"Grant {ROLE_TO_GRANT} to {env['ROLE']} to write it.")
            return
        status, result, completed_at, message = "CREATED", None, None, None
    else:
        if not state_file.exists():
            fail(f"{state_file} not found: run 'write-report.sh {report_dir} created' first")
        state = json.loads(state_file.read_text())
        if state["skipped"]:
            print("Snowsight benchmark report skipped (see the created step).")
            return
        status, result, completed_at, message = "RUNNING", None, None, None
        state["queries"] = queries(state["queries"])
        if command == "running":
            if args:
                fail(f"unexpected arguments: {args}")
        elif command == "iteration":
            if len(args) != 3 or not all(a.isdigit() for a in args):
                fail("usage: iteration <N> <RUN_START> <RUN_END> (epoch seconds)")
            number, run_start, run_end = (int(a) for a in args)
            state["tests"] = [t for t in state["tests"] if t["attempt_number"] != number]
            state["tests"].append(iteration(state, number, run_start, run_end))
            state["tests"].sort(key=lambda t: t["attempt_number"])
        elif command == "final":
            message = option(args, "--message")
            if not args or args[0] not in ("COMPLETED", "ERROR", "CANCELLED"):
                fail("usage: final <COMPLETED|ERROR|CANCELLED> [SUCCESS|FAIL] [--message TEXT]")
            status = args[0]
            result = args[1] if len(args) > 1 else {"COMPLETED": "SUCCESS", "CANCELLED": "CANCELLED"}.get(status)
            if len(args) > 2 or result not in ("SUCCESS", "FAIL", "CANCELLED", None):
                fail(f"unexpected arguments: {args}")
            completed_at = now
        else:
            fail(f"unknown command {command!r}")
    if command == "created":
        state["queries"] = queries([])
    payload = {
        "report_version": 1, "benchmark_id": state["benchmark_id"], "name": env["SOLUTION_NAME"],
        "tags": {"source": "interactive-benchmark-skill"}, "description": state["description"],
        "status": status, "result": result, "requested_concurrency": int(env["LOCUST_USERS"]),
        "runtime_seconds": seconds(env["LOCUST_RUN_TIME"]), "started_at": state["started_at"],
        "completed_at": completed_at, "error_code": {"ERROR": "ERROR", "CANCELLED": "CANCELLED"}.get(status),
        "error_message": message, "queries": state["queries"], "tests": state["tests"],
    }
    # "$" only occurs inside JSON strings, where \u0024 means the same; the body cannot end the $$ literal.
    body = json.dumps(payload, default=str).replace("$", "\\u0024")
    reply = sql(f"USE WAREHOUSE {env['DEPLOY_WAREHOUSE']};\nCALL {PROCEDURE}(PARSE_JSON($${body}$$));")
    state_file.write_text(json.dumps(state, indent=2, default=str))
    outcome = next(iter(reply[0].values())) if reply else "{}"
    outcome = json.loads(outcome) if isinstance(outcome, str) else outcome
    print(f"Benchmark report {state['benchmark_id']}: {status} ({outcome.get('result', 'no reply')}).")


try:
    main(sys.argv[1:])
except RuntimeError as exc:
    fail(f"Snowflake call failed: {exc}")
PYEOF
