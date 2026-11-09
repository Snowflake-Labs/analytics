from __future__ import annotations

from pathlib import Path

from bench_core import cli
from bench_core.spec import BenchmarkSpec

ROOT = Path(__file__).resolve().parent.parent.parent

QUERY_IDS = tuple(str(n) for n in range(1, 23))


def _stem_to_id(stem: str) -> str:
    """query_01 -> '1'"""
    return str(int(stem.split("_")[1]))


def _expand(token: str) -> list[str] | None:
    """'17' / '017' / 'Q17' -> ['17']"""
    token = token.strip().removeprefix("Q").removeprefix("q")
    if not token.isdigit():
        return None
    qid = str(int(token))
    return [qid] if qid in QUERY_IDS else None


SPEC = BenchmarkSpec(
    name="TPC-H",
    cli_name="iw-tpch",
    wrapper="./iwtpch.sh",
    root=ROOT,
    schema_prefix="TPCH_SF",
    results_schema="TPCH_RESULTS",
    tables=("LINEITEM", "ORDERS", "CUSTOMER", "PART", "PARTSUPP", "SUPPLIER", "NATION", "REGION"),
    query_ids=QUERY_IDS,
    stem_to_id=_stem_to_id,
    expand_query_token=_expand,
    query_help="Run a single query by number (1–22), e.g. --query 17",
    queries_help="Comma-separated query numbers to run, e.g. 2,11,15 (default: all 22)",
    reference_file="tpc-h-results-1GB.json",
    default_solution="TPCH",
)


def main(argv: list[str] | None = None) -> int:
    return cli.main(SPEC, argv)
