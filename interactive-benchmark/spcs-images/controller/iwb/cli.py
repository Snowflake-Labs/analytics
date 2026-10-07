"""`python -m iwb run --config <file>`: set up, run, measure and tear down one benchmark."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import sys
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from iwb import config as config_module
from iwb import metrics, runner, warehouse, workload
from iwb.config import ConfigError
from iwb.events import Events
from iwb.snow import connect

EXIT_OK = 0
EXIT_VERDICT_FAIL = 20
EXIT_INVALID = 30
EXIT_INFRA = 40
EXIT_CRASH = 1
RESULT_SCHEMA_VERSION = 1


def _run_id(name: object) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    prefix = name.upper() if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,30}", name) else "IWB"
    return f"{prefix}_{stamp}_{secrets.token_hex(2).upper()}"


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def run(config_path: str, results_root: str, connection_name: str) -> int:
    raw_name = None
    try:
        raw_name = json.loads(Path(config_path).read_text()).get("name")
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    run_id = _run_id(raw_name)
    results_dir = Path(results_root) / run_id
    events = Events(run_id)
    result: dict = {"schema_version": RESULT_SCHEMA_VERSION, "run_id": run_id,
                    "started_at": datetime.now(UTC).isoformat(timespec="seconds")}
    exit_code = EXIT_CRASH
    snow = None
    wh = None
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        events.emit("VALIDATE", "started", config=config_path)
        cfg = config_module.load(config_path)
        snow = connect(connection_name)
        queries = workload.resolve(cfg, snow)
        tables = workload.discover_tables(snow, queries)
        result["workload"] = [
            {"id": q.id, "source": q.source, "weight_pct": q.weight, "context": asdict(q.context)}
            for q in queries
        ]
        events.emit("VALIDATE", "completed", queries=len(queries), tables=sorted(tables))

        events.emit("SETUP_WAREHOUSE", "started")
        wh = warehouse.prepare(cfg, snow, tables, run_id, events)
        warehouse.activate(cfg, snow, wh)
        warehouse.ensure_compiles(snow, wh, queries, events)
        events.emit("SETUP_WAREHOUSE", "completed", warehouse=wh.name, created=wh.created,
                    tables=sorted(wh.tables))

        events.emit("WARM", "started")
        runner.warm(snow, wh, queries, events)
        events.emit("WARM", "completed")

        load = runner.run_load(cfg, queries, wh, run_id, connection_name, results_dir, events)
        result["baseline"] = load.baseline
        result["client"] = load.client
        result["outcome"] = load.outcome

        if load.outcome in ("PASS", "BENCHMARK_FAIL") and load.window_end:
            events.emit("MEASURE", "started")
            result["window"] = {"start": load.window_start, "end": load.window_end}
            result["server"] = metrics.server_side(
                snow, cfg.context.database, wh.name, cfg.fallback_warehouse, run_id,
                load.window_start, load.window_end, events,
            )
            events.emit("MEASURE", "completed", server=result["server"])

        exit_code = {"PASS": EXIT_OK, "BENCHMARK_FAIL": EXIT_VERDICT_FAIL}.get(load.outcome, EXIT_INFRA)
    except ConfigError as exc:
        result["error"] = str(exc)
        events.emit("VALIDATE", "failed", error=str(exc))
        exit_code = EXIT_INVALID
    except SystemExit as exc:
        result["error"] = f"terminated (exit {exc.code})"
        events.emit("RUN", "failed", error=result["error"])
        exit_code = int(exc.code) if isinstance(exc.code, int) else EXIT_CRASH
    except Exception as exc:  # noqa: BLE001 - reported as an event and in result.json
        result["error"] = f"{type(exc).__name__}: {exc}"
        events.emit("RUN", "failed", error=result["error"], traceback=traceback.format_exc())
        exit_code = EXIT_CRASH
    finally:
        if snow is not None and wh is not None:
            try:
                warehouse.drop(snow, wh, events)
            except Exception as exc:  # noqa: BLE001
                events.emit("TEARDOWN", "failed", warehouse=wh.name, error=str(exc))
                exit_code = exit_code or EXIT_CRASH
        if wh is not None:
            result["warehouse"] = {"name": wh.name, "created": wh.created, "tables": sorted(wh.tables)}
        if snow is not None:
            try:
                snow.close()
            except Exception:  # noqa: BLE001 - the run is over; never lose result.json for this
                pass
        result["exit_code"] = exit_code
        result["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
        events.emit("RUN", "completed" if exit_code == EXIT_OK else "failed", exit_code=exit_code,
                    result=str(results_dir / "result.json"))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iwb")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Run one benchmark from a config file")
    run_p.add_argument("--config", required=True)
    run_p.add_argument("--results-dir", default=os.environ.get("IWB_RESULTS_DIR", "iwb-results"))
    run_p.add_argument("--connection", default=os.environ.get("IWB_CONNECTION", "default"),
                       help="connections.toml entry (the sandbox renders `default`)")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    return run(args.config, args.results_dir, args.connection)


if __name__ == "__main__":
    sys.exit(main())
