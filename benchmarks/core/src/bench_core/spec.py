from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkSpec:
    """Everything that differs between benchmarks (TPC-H, SSB, ...).

    The shared harness (setup / list / run / teardown, timing, validation,
    result storage) is driven entirely by this description.
    """

    # Display name, e.g. "TPC-H".
    name: str
    # Console script / version banner name, e.g. "iw-tpch".
    cli_name: str
    # Wrapper script mentioned in hints, e.g. "./iwtpch.sh".
    wrapper: str
    # Benchmark folder: holds .env, pyproject.toml, queries/, sql/, results/.
    root: Path
    # Benchmark schema prefix; schemas are <prefix><scale>[_IT|_ICE], e.g. "TPCH_SF".
    schema_prefix: str
    # Schema holding the BENCHMARK_RUNS hybrid table, e.g. "TPCH_RESULTS".
    results_schema: str
    # Tables attached to the interactive warehouse.
    tables: tuple[str, ...]
    # Valid query ids in run order, e.g. ("1", ..., "22") or ("1.1", ..., "4.3").
    query_ids: tuple[str, ...]
    # Query file stem -> query id, e.g. "query_01" -> "1".
    stem_to_id: Callable[[str], str]
    # Normalize one --query/--queries token to query ids (None if invalid).
    # Defaults to an exact match against query_ids.
    expand_query_token: Callable[[str], list[str] | None] | None = None
    # Help text for --query and --queries.
    query_help: str = "Run a single query by id"
    queries_help: str = "Comma-separated query ids to run"
    # Reference rows for SF1 validation, relative to root.
    reference_file: str = ""
    # Extra scripts (in root/sql) run before the table setup scripts.
    pre_setup_scripts: tuple[str, ...] = field(default_factory=tuple)
    # Extra scripts (in root/sql) run after the table teardown scripts when
    # --tables-type all (and the database is not dropped).
    post_teardown_scripts: tuple[str, ...] = field(default_factory=tuple)
    default_solution: str = "BENCH"

    def query_label(self, stem: str) -> str:
        return f"Q{self.stem_to_id(stem)}"

    def expand(self, token: str) -> list[str] | None:
        if self.expand_query_token is not None:
            return self.expand_query_token(token)
        token = token.strip()
        return [token] if token in self.query_ids else None
