"""Warm up, then run the API server and the Locust baseline + benchmark phases locally."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from iwb.budget import API_READY_TIMEOUT_SECONDS, locust_timeout_seconds
from iwb.config import Config
from iwb.events import Events
from iwb.snow import Snow
from iwb.warehouse import Warehouse
from iwb.workload import Query, ident, use_context

WARMUP_ROUNDS = 3
STOP_GRACE_SECONDS = 30
# Settings the controller owns; a stray value in the host environment must not change the gates.
CONTROLLED_ENV_PREFIXES = ("BASELINE_", "BENCHMARK_MAX_", "POOL_", "LOCUST_", "API_READY_")
LOCUST_EXIT = {0: "PASS", 1: "API_NOT_READY", 2: "BASELINE_FAIL", 3: "BENCHMARK_FAIL"}
# Locust log lines start with a local-time "[YYYY-mm-dd HH:MM:SS,mmm]".
LOCUST_LOG_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})\]")


@dataclass(frozen=True)
class LoadResult:
    outcome: str
    baseline: dict | None
    client: dict | None
    window: tuple[float, float] | None


def warm(snow: Snow, wh: Warehouse, queries: list[Query], events: Events) -> None:
    snow.execute(f"USE WAREHOUSE {ident(wh.name)}")
    for round_no in range(1, WARMUP_ROUNDS + 1):
        slowest_ms = 0
        for q in queries:
            use_context(snow, q.context)
            t0 = time.perf_counter()
            snow.execute(q.sql)
            slowest_ms = max(slowest_ms, round((time.perf_counter() - t0) * 1000))
        events.emit("WARM", "progress", round=round_no, of=WARMUP_ROUNDS, slowest_ms=slowest_ms)


def _child_env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(CONTROLLED_ENV_PREFIXES)}
    env.update(overrides)
    return env


def _stop(proc: subprocess.Popen) -> None:
    """Stop a child and everything in its process group (uvicorn workers included)."""
    if proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGTERM, STOP_GRACE_SECONDS), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sizing(users: int) -> tuple[int, int]:
    """Uvicorn workers and per-worker pool size; each worker holds its own pool."""
    workers = 1 if users <= 100 else min(4, math.ceil(users / 100))
    return workers, math.ceil(users / workers) + 5


def parse_stats_row(path: Path, name: str) -> dict | None:
    if not path.exists():
        return None
    with path.open() as f:
        for row in csv.DictReader(f):
            if row["Name"] == name:
                return {
                    "requests": int(row["Request Count"]),
                    "failures": int(row["Failure Count"]),
                    "requests_per_s": float(row["Requests/s"]),
                    "avg_ms": float(row["Average Response Time"]),
                    "p50_ms": float(row["50%"]),
                    "p95_ms": float(row["95%"]),
                    "p99_ms": float(row["99%"]),
                    "max_ms": float(row["Max Response Time"]),
                }
    return None


def measured_window(log: Path) -> tuple[float, float] | None:
    """Epoch seconds of the stats reset and the run-time stop in a phase's Locust log.

    The stats-history CSV is flushed every few seconds, so its last row can trail the
    real end of the run; the log lines are exact.
    """
    if not log.exists():
        return None
    start = end = None
    for line in log.read_text().splitlines():
        match = LOCUST_LOG_TS.match(line)
        if not match:
            continue
        ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp() + int(match.group(2)) / 1000
        if "Resetting stats" in line:
            start = ts
        elif "--run-time limit reached" in line:
            end = ts
    return (start, end) if start is not None and end is not None else None


def run_load(
    cfg: Config,
    queries: list[Query],
    wh: Warehouse,
    run_id: str,
    connection_name: str,
    results_dir: Path,
    events: Events,
) -> LoadResult:
    api_python = os.environ.get("IWB_API_PYTHON", "/opt/venvs/api/bin/python")
    api_server = os.environ.get("IWB_API_SERVER", "/app/api/server.py")
    locust_entrypoint = os.environ.get("IWB_LOCUST_ENTRYPOINT", "/app/locust/entrypoint.sh")
    workload_file = results_dir / "workload.json"
    workload_file.write_text(json.dumps({"queries": [
        {"id": q.id, "sql": q.sql, "weight": q.weight,
         "database": q.context.database, "schema": q.context.schema}
        for q in queries
    ]}, indent=2))

    port = _free_port()
    workers, pool_size = _sizing(cfg.concurrent_users)
    api_env = _child_env(**{
        "CONNECTION_NAME": connection_name,
        "SOLUTION_NAME": run_id,
        "QUERY_TAG": run_id,
        "SNOWFLAKE_DATABASE": cfg.context.database,
        "INTERACTIVE_SCHEMA": cfg.context.schema,
        "INTERACTIVE_WAREHOUSE": wh.name,
        "BENCHMARK_WORKLOAD_FILE": str(workload_file),
        "PORT": str(port),
        "WORKERS": str(workers),
        "POOL_SIZE": str(pool_size),
        "POOL_WARMUP": str(min(pool_size, 10)),
    })
    locust_env = _child_env(**{
        "LOCUST_HOST": f"http://127.0.0.1:{port}",
        "LOCUST_USERS": str(cfg.concurrent_users),
        "LOCUST_SPAWN": str(min(cfg.concurrent_users, 10)),
        "LOCUST_RUN_TIME": f"{cfg.run_seconds}s",
        "LOCUST_WEB_PORT": str(_free_port()),
        "API_READY_TIMEOUT_SECONDS": str(API_READY_TIMEOUT_SECONDS),
        "BENCHMARK_EXIT_AFTER_RUN": "1",
        "BENCHMARK_RESULTS_DIR": str(results_dir),
    })
    locust_timeout = locust_timeout_seconds(cfg.run_seconds, cfg.concurrent_users)

    events.emit("LOAD", "started", users=cfg.concurrent_users, run_seconds=cfg.run_seconds,
                api_workers=workers, api_pool_size=pool_size)
    with (results_dir / "api.log").open("w") as api_log, (results_dir / "locust.log").open("w") as locust_log:
        api = subprocess.Popen(
            [api_python, api_server], env=api_env, start_new_session=True,
            cwd=str(Path(api_server).parent), stdout=api_log, stderr=subprocess.STDOUT,
        )
        locust = None
        try:
            locust = subprocess.Popen(
                ["bash", locust_entrypoint], env=locust_env, start_new_session=True,
                stdout=locust_log, stderr=subprocess.STDOUT,
            )
            try:
                rc = locust.wait(timeout=locust_timeout)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Locust did not finish within {locust_timeout}s") from exc
        finally:
            if locust is not None:
                _stop(locust)
            _stop(api)

    outcome = LOCUST_EXIT.get(rc, f"LOCUST_EXIT_{rc}")
    result = LoadResult(
        outcome=outcome,
        baseline=parse_stats_row(results_dir / "baseline_stats_stats.csv", "/api/run/baseline"),
        client=parse_stats_row(results_dir / "locust_stats_stats.csv", "/api/run/interactive"),
        window=measured_window(results_dir / "locust_run.log"),
    )
    events.emit("LOAD", "completed" if outcome == "PASS" else "failed", outcome=outcome,
                client=result.client, baseline=result.baseline)
    return result
