from __future__ import annotations

import re
from pathlib import Path

from bench_core import cli
from bench_core.spec import BenchmarkSpec

ROOT = Path(__file__).resolve().parent.parent.parent

# SSB defines 13 queries in 4 "flights": Q1.1-Q1.3, Q2.1-Q2.3, Q3.1-Q3.4, Q4.1-Q4.3.
QUERY_IDS = (
    "1.1", "1.2", "1.3",
    "2.1", "2.2", "2.3",
    "3.1", "3.2", "3.3", "3.4",
    "4.1", "4.2", "4.3",
)

_ID_RE = re.compile(r"^[Qq]?(\d)(?:[._](\d))?$")


def _stem_to_id(stem: str) -> str:
    """query_2_1 -> '2.1'"""
    _, flight, num = stem.split("_")
    return f"{int(flight)}.{int(num)}"


def _expand(token: str) -> list[str] | None:
    """'2.1' / 'Q2.1' / '2_1' -> ['2.1']; a bare flight '3' -> ['3.1', ..., '3.4']."""
    m = _ID_RE.match(token.strip())
    if not m:
        return None
    flight, num = m.groups()
    if num is None:
        return [q for q in QUERY_IDS if q.startswith(f"{flight}.")] or None
    qid = f"{flight}.{num}"
    return [qid] if qid in QUERY_IDS else None


SPEC = BenchmarkSpec(
    name="SSB",
    cli_name="iw-ssb",
    wrapper="./iwssb.sh",
    root=ROOT,
    schema_prefix="SSB_SF",
    results_schema="SSB_RESULTS",
    tables=("LINEORDER", "LINEORDER_FLAT", "CUSTOMER", "SUPPLIER", "PART", "DATES"),
    query_ids=QUERY_IDS,
    stem_to_id=_stem_to_id,
    expand_query_token=_expand,
    query_help="Run a single query by id (1.1–4.3), e.g. --query 3.4",
    queries_help="Comma-separated query ids and/or whole flights to run, e.g. 1.1,2.3,4 (default: all 13)",
    reference_file="ssb-results-1GB.json",
    # Every table format is CTAS'd from SSB views derived from the TPC-H sample data.
    pre_setup_scripts=("setup_source_views.sql",),
    post_teardown_scripts=("teardown_source_views.sql",),
    default_solution="SSB",
)


def main(argv: list[str] | None = None) -> int:
    return cli.main(SPEC, argv, workloads=("original", "modern", "flat"))
