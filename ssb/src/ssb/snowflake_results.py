from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import snowflake.connector

from src.ssb import config as _cfg

RESULTS_SCHEMA = "SSB_RESULTS"
RESULTS_TABLE = "BENCHMARK_RUNS"
RESULTS_SEQUENCE = "BENCHMARK_RUN_SEQ"
# Hybrid tables can't be written through an interactive warehouse, so results
# are always saved using a standard warehouse.
RESULTS_WAREHOUSE = os.getenv("RESULTS_WAREHOUSE", "COMPUTE_WH")
SETUP_SCRIPT = "setup_results_table.sql"
TEARDOWN_SCRIPT = "teardown_results_table.sql"

_STATS = ("total", "avg", "median", "min", "max", "p95")


def check_results_warehouse(conn, warehouse: str = RESULTS_WAREHOUSE) -> None:
    """Raise RuntimeError if ``warehouse`` can't be used for saving results."""
    with conn.cursor() as cur:
        try:
            cur.execute(f"USE WAREHOUSE {warehouse}")
        except snowflake.connector.errors.ProgrammingError as exc:
            raise RuntimeError(
                f"Results warehouse {warehouse} is not available: {exc.msg}\n"
                "It is required by --save-to-snowflake. Grant USAGE on it, or set "
                "RESULTS_WAREHOUSE in .env to another standard warehouse."
            ) from exc


def results_table_fqn() -> str:
    return f"{_cfg.BENCHMARK_DATABASE}.{RESULTS_SCHEMA}.{RESULTS_TABLE}"


def save_run_to_snowflake(conn, json_path: Path, run_ts: datetime | None = None,
    warehouse: str = RESULTS_WAREHOUSE) -> int:
    """Insert one row for the run described by ``json_path``; returns the run id."""
    doc = json.loads(json_path.read_text())
    s = doc["summary"]
    run_ts = run_ts or datetime.now(UTC)

    stat_cols = [f"{p}_ELAPSED_S_{k.upper()}" for p in ("CLIENT", "SERVER") for k in _STATS]
    stat_vals = [
        s[f"{p}_elapsed_s"][k] for p in ("client", "server") for k in _STATS
    ]
    target_fs = s.get("target_file_size")

    sql = f"""
        INSERT INTO {results_table_fqn()} (
          BENCHMARK_RUN_ID, BENCHMARK_RUN_TS, WAREHOUSE_TYPE, TABLES_TYPE, SCALE, WORKLOAD,
          TOTAL_QUERIES, SUCCESSFUL, FAILED, VALIDATION_PASSED, VALIDATION_FAILED,
          {", ".join(stat_cols)},
          CONNECTION, DATABASE_NAME, SCHEMA_NAME, WAREHOUSE, WAREHOUSE_SIZE, SERVER_VERSION,
          TARGET_FILE_SIZE, RESULTS_JSON
        )
        SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          {", ".join(["%s"] * len(stat_cols))},
          %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s)
    """
    params = [
        run_ts.strftime("%Y-%m-%d %H:%M:%S.%f"),
        doc["warehouse_type"],
        doc["tables_type"],
        doc["scale"],
        doc["workload"],
        s["total_queries"],
        s["successful"],
        s["failed"],
        s.get("validation_passed"),
        s.get("validation_failed"),
        *stat_vals,
        s.get("connection"),
        s.get("database"),
        s.get("schema"),
        s.get("warehouse"),
        s.get("warehouse_size"),
        s.get("server_version"),
        json.dumps(target_fs) if target_fs is not None else None,
        json.dumps(doc),
    ]
    with conn.cursor() as cur:
        cur.execute(f"USE WAREHOUSE {warehouse}")
        # Fetch the id up front so it can be reported back to the caller.
        cur.execute(
            f"SELECT {_cfg.BENCHMARK_DATABASE}.{RESULTS_SCHEMA}.{RESULTS_SEQUENCE}.NEXTVAL"
        )
        run_id = cur.fetchone()[0]
        cur.execute(sql, [run_id, *params])
    return run_id
