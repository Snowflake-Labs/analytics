"""`python -m iwb run --config <file>`: set up, run, measure and tear down one benchmark."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import sys
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iwb import config as config_module
from iwb import metrics, runner, warehouse, workload
from iwb.config import ConfigError
from iwb.events import Events, now_iso
from iwb.snow import connect

EXIT_OK = 0
EXIT_VERDICT_FAIL = 20
EXIT_INVALID = 30
EXIT_INFRA = 40
EXIT_CRASH = 1
RESULT_SCHEMA_VERSION = 1


def _run_id(name: object) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    prefix = name.upper() if isinstance(name, str) and config_module.NAME_PATTERN.fullmatch(name) else "IWB"
    return f"{prefix}_{stamp}_{secrets.token_hex(2).upper()}"


def _terminate(signum: int, _frame: object) -> None:
    """The first stop request ends the run; later ones must not interrupt its cleanup."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)
    raise SystemExit(128 + signum)


def _read(config_path: str) -> Any:
    try:
        return json.loads(Path(config_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def run(config_path: str, results_root: str, connection_name: str) -> int:
    raw = _read(config_path)
    run_id = _run_id(raw.get("name") if isinstance(raw, dict) else None)
    # Absolute: the API server, which reads workload.json from here, runs in another directory.
    results_dir = Path(results_root).resolve() / run_id
    events = Events(run_id)
    result: dict = {"schema_version": RESULT_SCHEMA_VERSION, "run_id": run_id,
                    "started_at": now_iso()}
    exit_code = EXIT_CRASH
    snow = None
    wh = None
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        events.emit("VALIDATE", "started", config=config_path)
        # load() reports why the file could not be read.
        cfg = config_module.parse(raw) if raw is not None else config_module.load(config_path)
        snow = connect(connection_name)
        queries = workload.resolve(cfg, snow)
        tables = workload.discover_tables(snow, queries)
        result["workload"] = [
            {"id": q.id, "source": q.source, "weight_pct": q.weight, "context": asdict(q.context)}
            for q in queries
        ]
        events.emit("VALIDATE", "completed", queries=len(queries), tables=sorted(tables))

        events.emit("SETUP_WAREHOUSE", "started")
        wh = warehouse.planned(cfg, run_id)
        warehouse.prepare(cfg, snow, tables, wh, run_id, events)
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

        if load.outcome in ("PASS", "BENCHMARK_FAIL"):
            if not load.window:
                raise RuntimeError("locust_run.log has no measured window; server-side metrics cannot be computed")
            events.emit("MEASURE", "started")
            start, end = load.window
            result["window"] = {"start": start, "end": end}
            result["server"] = metrics.server_side(
                snow, cfg.context.database, wh.name, wh.fallback, run_id, start, end,
            )
            events.emit("MEASURE", "completed", server=result["server"])

        exit_code = {"PASS": EXIT_OK, "BENCHMARK_FAIL": EXIT_VERDICT_FAIL}.get(load.outcome, EXIT_INFRA)
    except ConfigError as exc:
        result["error"] = str(exc)
        events.emit(events.step, "failed", error=str(exc))
        exit_code = EXIT_INVALID
    except SystemExit as exc:
        result["error"] = f"terminated (exit {exc.code})"
        events.emit(events.step, "failed", error=result["error"])
        exit_code = int(exc.code) if isinstance(exc.code, int) else EXIT_CRASH
    except Exception as exc:  # noqa: BLE001 - reported as an event and in result.json
        result["error"] = f"{type(exc).__name__}: {exc}"
        events.emit(events.step, "failed", error=result["error"], traceback=traceback.format_exc())
        exit_code = EXIT_CRASH
    finally:
        # A second stop request must not interrupt teardown or the result.
        handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGTERM, signal.SIGINT)}
        if snow is not None and wh is not None:
            try:
                warehouse.teardown(snow, wh, events)
            except Exception as exc:  # noqa: BLE001
                result["teardown_error"] = f"{wh.name}: {exc}"
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
        result["finished_at"] = now_iso()
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
        events.emit("RUN", "completed" if exit_code == EXIT_OK else "failed", exit_code=exit_code,
                    result=str(results_dir / "result.json"))
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iwb")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Run one benchmark from a config file")
    run_p.add_argument("--config", required=True)
    run_p.add_argument("--results-dir", default=os.environ.get("IWB_RESULTS_DIR", "/var/tmp/iwb/results"))
    run_p.add_argument("--connection", default=os.environ.get("IWB_CONNECTION", "default"),
                       help="connections.toml entry (the sandbox renders `default`)")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    return run(args.config, args.results_dir, args.connection)


if __name__ == "__main__":
    sys.exit(main())
