from __future__ import annotations

import argparse
import sys

from bench_core import config as _cfg
from bench_core.commands import cmd_list, cmd_run, cmd_setup, cmd_teardown
from bench_core.config import (
    DEFAULT_TABLE_TYPE,
    DEFAULT_WAREHOUSE_TYPE,
    DEFAULT_WORKLOAD,
    SCALES,
    TABLE_TYPES,
    WAREHOUSE_TYPES,
    configure,
    load_solution_name,
    project_version,
)
from bench_core.spec import BenchmarkSpec


def _add_connection_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--connection",
        default=None,
        help="Snowflake connection name from connections.toml (default: CONNECTION_NAME env var)",
    )


def _add_solution_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--solution",
        default=None,
        help=f"Solution name used to derive all object names (overrides SOLUTION_NAME env var, default: {_cfg.SPEC.default_solution})",
    )


def build_parser() -> argparse.ArgumentParser:
    name = _cfg.SPEC.name
    DEFAULT_SCALE = _cfg.DEFAULT_SCALE
    WORKLOADS = _cfg.WORKLOADS
    p = argparse.ArgumentParser(description=f"{name} benchmark on Snowflake v{project_version()}")
    sub = p.add_subparsers(dest="command", required=True)

    setup_p = sub.add_parser("setup", help="Create database, tables and warehouses")
    setup_p.add_argument(
        "--scale",
        choices=SCALES,
        default=DEFAULT_SCALE,
        help=f"{name} scale factor to load: 1, 10, 100, or 1000 (default {DEFAULT_SCALE})",
    )
    setup_p.add_argument(
        "--tables-type",
        choices=(*TABLE_TYPES, "all"),
        default=None,
        help="Table format to set up: standard, interactive, iceberg, or all "
        "(default all; with --save-to-snowflake, none unless specified)",
    )
    setup_p.add_argument(
        "--warehouse-type",
        choices=(*WAREHOUSE_TYPES, "all"),
        default=None,
        help="Warehouse type to create: standard, interactive, or all "
        "(default all; with --save-to-snowflake, none unless specified)",
    )
    setup_p.add_argument(
        "--target-file-size",
        default="AUTO",
        help="TARGET_FILE_SIZE for iceberg tables (default AUTO)",
    )
    setup_p.add_argument(
        "--save-to-snowflake",
        action="store_true",
        default=False,
        help=f"Also create the hybrid table used to store benchmark results ({_cfg.SPEC.results_schema}.BENCHMARK_RUNS)",
    )
    setup_p.add_argument(
        "--recreate-results-table",
        action="store_true",
        default=False,
        help="Drop and recreate the results hybrid table and its sequence (implies "
        "--save-to-snowflake; existing results are lost)",
    )
    _add_connection_arg(setup_p)
    _add_solution_arg(setup_p)

    teardown_p = sub.add_parser("teardown", help="Drop benchmark schemas and warehouses")
    teardown_p.add_argument(
        "--scale",
        choices=SCALES,
        default=DEFAULT_SCALE,
        help=f"{name} scale factor to tear down: 1, 10, 100, or 1000 (default {DEFAULT_SCALE})",
    )
    teardown_p.add_argument(
        "--tables-type",
        choices=(*TABLE_TYPES, "all"),
        default="all",
        help="Table format to tear down: standard, interactive, iceberg, or all (default all)",
    )
    teardown_p.add_argument(
        "--warehouse-type",
        choices=(*WAREHOUSE_TYPES, "all"),
        default="all",
        help="Warehouse type to drop: standard, interactive, or all (default all)",
    )
    teardown_p.add_argument(
        "--drop-database",
        action="store_true",
        default=False,
        help="Also drop the entire benchmark database (default: only drop selected schemas/warehouses)",
    )
    _add_connection_arg(teardown_p)
    _add_solution_arg(teardown_p)

    list_p = sub.add_parser("list", help="List databases and warehouses created for this solution")
    _add_connection_arg(list_p)
    _add_solution_arg(list_p)

    run_p = sub.add_parser("run", help=f"Run the {name} benchmark")
    run_p.add_argument(
        "--warehouse-type",
        choices=WAREHOUSE_TYPES,
        default=DEFAULT_WAREHOUSE_TYPE,
        help=f"Warehouse engine: interactive or standard (default {DEFAULT_WAREHOUSE_TYPE})",
    )
    run_p.add_argument(
        "--tables-type",
        choices=TABLE_TYPES,
        default=DEFAULT_TABLE_TYPE,
        help=f"Table format to query: standard, interactive, or iceberg (default {DEFAULT_TABLE_TYPE})",
    )
    run_p.add_argument(
        "--scale",
        choices=SCALES,
        default=DEFAULT_SCALE,
        help=f"{name} scale factor: 1, 10, 100, or 1000 (default {DEFAULT_SCALE})",
    )
    run_p.add_argument(
        "--workload",
        choices=WORKLOADS,
        default=DEFAULT_WORKLOAD,
        help=f"Query set to run: {', '.join(WORKLOADS)} (default {DEFAULT_WORKLOAD})",
    )
    run_p.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Executions per query, keeping the best (min) time (default 3)",
    )
    run_p.add_argument(
        "--avg",
        action="store_true",
        default=False,
        help="Report average execution time instead of best. Runs a warmup query first, then averages the repeats.",
    )
    run_p.add_argument(
        "--iterations", type=int, default=1, help="Number of full passes (default 1)"
    )
    run_p.add_argument(
        "--query",
        type=str,
        metavar="ID",
        default=None,
        help=_cfg.SPEC.query_help,
    )
    run_p.add_argument(
        "--queries",
        type=str,
        default=None,
        help=_cfg.SPEC.queries_help,
    )
    run_p.add_argument(
        "--database",
        default=None,
        help="Snowflake database to use (overrides the default)",
    )
    run_p.add_argument(
        "--schema",
        default=None,
        help="Snowflake schema to use (overrides the default for --tables-type)",
    )
    run_p.add_argument(
        "--warehouse",
        default=None,
        help="Snowflake warehouse to use (overrides the default for --warehouse-type and --scale)",
    )
    run_p.add_argument(
        "--save-to-snowflake",
        action="store_true",
        default=False,
        help="Save the run summary and full JSON results into the Snowflake hybrid table",
    )
    _add_connection_arg(run_p)
    _add_solution_arg(run_p)

    return p


COMMANDS = {
    "setup": cmd_setup,
    "list": cmd_list,
    "run": cmd_run,
    "teardown": cmd_teardown,
}


def main(spec: BenchmarkSpec, argv: list[str] | None = None, *,
         workloads: tuple[str, ...] | None = None) -> int:
    configure(spec, workloads)
    if argv is None:
        argv = sys.argv[1:]
    if "-V" in argv or "--version" in argv:
        print(f"{spec.cli_name} {project_version()}")
        return 0
    args = build_parser().parse_args(argv)
    if getattr(args, "solution", None):
        load_solution_name(args.solution)
    print(f"{spec.cli_name} {project_version()} [{_cfg.SOLUTION_NAME}]")
    return COMMANDS[args.command](args)
