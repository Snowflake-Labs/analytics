from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

from bench_core.spec import BenchmarkSpec

CORE_SQL_DIR = Path(__file__).resolve().parent / "sql"

WORKLOADS = ("original", "modern")
DEFAULT_WORKLOAD = "original"

WAREHOUSE_TYPES = ("interactive", "standard")
DEFAULT_WAREHOUSE_TYPE = "interactive"
TABLE_TYPES = ("interactive", "standard", "iceberg")
DEFAULT_TABLE_TYPE = "standard"


class ScaleConfig(TypedDict):
    load_warehouse: str
    benchmark_warehouse: str


# Scale factors of the TPC-H source data in SNOWFLAKE_SAMPLE_DATA.
SCALES: dict[str, ScaleConfig] = {
    "1": {"load_warehouse": "SMALL", "benchmark_warehouse": "SMALL"},
    "10": {"load_warehouse": "LARGE", "benchmark_warehouse": "MEDIUM"},
    "100": {"load_warehouse": "XLARGE", "benchmark_warehouse": "LARGE"},
    "1000": {"load_warehouse": "XXLARGE", "benchmark_warehouse": "XXLARGE"},
}

SQL_SCALE = "{{SCALE}}"
SQL_SOLUTION_NAME = "{{SOLUTION_NAME}}"
SQL_LOAD_WH_SIZE = "{{LOAD_WH_SIZE}}"
SQL_BENCH_WH_SIZE = "{{BENCH_WH_SIZE}}"
SQL_SCHEMA_NAME = "{{SCHEMA_NAME}}"
SQL_EXTERNAL_VOLUME = "{{EXTERNAL_VOLUME}}"
SQL_TARGET_FILE_SIZE = "{{TARGET_FILE_SIZE}}"
SQL_SCHEMA_PREFIX = "{{SCHEMA_PREFIX}}"
SQL_RESULTS_SCHEMA = "{{RESULTS_SCHEMA}}"
SQL_TABLE_LIST = "{{TABLE_LIST}}"

# Set by configure(); read by the other modules via `config.<NAME>`.
SPEC: BenchmarkSpec
ROOT: Path
QUERIES_DIR: Path
SQL_DIR: Path
RESULTS_DIR: Path
EXPECTED_RESULTS_1GB_PATH: Path
SCHEMA_PREFIX = ""
DEFAULT_CONNECTION_NAME: str | None = None
DEFAULT_SCALE = "10"

SOLUTION_NAME = ""
BENCHMARK_DATABASE = ""
INTERACTIVE_WH_PREFIX = ""
STANDARD_WH_PREFIX = ""


def configure(spec: BenchmarkSpec, workloads: tuple[str, ...] | None = None) -> None:
    """Bind the shared harness to one benchmark. Must run before any command."""
    global SPEC, ROOT, QUERIES_DIR, SQL_DIR, RESULTS_DIR, EXPECTED_RESULTS_1GB_PATH
    global SCHEMA_PREFIX, DEFAULT_CONNECTION_NAME, DEFAULT_SCALE, WORKLOADS
    SPEC = spec
    ROOT = spec.root
    QUERIES_DIR = ROOT / "queries"
    SQL_DIR = ROOT / "sql"
    RESULTS_DIR = ROOT / "results"
    EXPECTED_RESULTS_1GB_PATH = ROOT / spec.reference_file
    SCHEMA_PREFIX = spec.schema_prefix
    if workloads is not None:
        WORKLOADS = workloads
    load_dotenv(ROOT / ".env", override=False)
    DEFAULT_CONNECTION_NAME = os.getenv("CONNECTION_NAME")
    DEFAULT_SCALE = os.getenv("DEFAULT_SCALE", "10")
    load_solution_name()


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def load_solution_name(name: str | None = None) -> None:
    """Re-derive all name constants from SOLUTION_NAME (env var or explicit override)."""
    global SOLUTION_NAME, BENCHMARK_DATABASE, INTERACTIVE_WH_PREFIX, STANDARD_WH_PREFIX
    if name is not None:
        os.environ["SOLUTION_NAME"] = name
    SOLUTION_NAME = os.getenv("SOLUTION_NAME", "")
    if not SOLUTION_NAME:
        print(
            "Error: SOLUTION_NAME is not set.\n"
            "Set it in your .env file or pass --solution on the command line.",
            file=sys.stderr,
        )
        sys.exit(1)
    BENCHMARK_DATABASE = f"{SOLUTION_NAME}_BENCH_DB"
    INTERACTIVE_WH_PREFIX = f"{SOLUTION_NAME}_BENCH_WH_INT"
    STANDARD_WH_PREFIX = f"{SOLUTION_NAME}_BENCH_WH_STD"


def sql_script_path(script_name: str) -> Path:
    """Benchmark-specific scripts (root/sql) override the shared core templates."""
    local = SQL_DIR / script_name
    return local if local.is_file() else CORE_SQL_DIR / script_name


def sql_substitutions_for_scale(scale: str) -> dict[str, str]:
    config = SCALES[scale]
    return {
        SQL_SOLUTION_NAME: SOLUTION_NAME,
        SQL_SCALE: scale,
        SQL_LOAD_WH_SIZE: config["load_warehouse"],
        SQL_BENCH_WH_SIZE: config["benchmark_warehouse"],
        SQL_SCHEMA_PREFIX: SCHEMA_PREFIX,
        SQL_RESULTS_SCHEMA: SPEC.results_schema,
    }


def table_list_sql(schema: str) -> str:
    """Fully-qualified, comma-separated table list for the interactive warehouse DDL."""
    return ",\n".join(f"    {BENCHMARK_DATABASE}.{schema}.{t}" for t in SPEC.tables)


def schema_for_scale(scale: str) -> str:
    return f"{SCHEMA_PREFIX}{scale}"


def interactive_schema_for_scale(scale: str) -> str:
    return f"{schema_for_scale(scale)}_IT"


def iceberg_schema_for_scale(scale: str) -> str:
    return f"{schema_for_scale(scale)}_ICE"


def schema_for_tables_type(tables_type: str, scale: str) -> str:
    if tables_type == "interactive":
        return interactive_schema_for_scale(scale)
    if tables_type == "iceberg":
        return iceberg_schema_for_scale(scale)
    return schema_for_scale(scale)


def warehouse_name_for_type(warehouse_type: str, scale: str) -> str:
    prefix = INTERACTIVE_WH_PREFIX if warehouse_type == "interactive" else STANDARD_WH_PREFIX
    return f"{prefix}_{scale}"


def target_context(
    warehouse_type: str,
    tables_type: str,
    scale: str,
    *,
    database: str | None = None,
    schema: str | None = None,
    warehouse: str | None = None,
) -> tuple[str, str, str]:
    """Return (database, schema, warehouse) for the requested types + scale."""
    return (
        database or BENCHMARK_DATABASE,
        schema or schema_for_tables_type(tables_type, scale),
        warehouse or warehouse_name_for_type(warehouse_type, scale),
    )
