from __future__ import annotations

import re
import sys

from src.ssb.config import QUERIES_DIR

# SSB defines 13 queries in 4 "flights": Q1.1-Q1.3, Q2.1-Q2.3, Q3.1-Q3.4, Q4.1-Q4.3.
QUERY_IDS = (
    "1.1", "1.2", "1.3",
    "2.1", "2.2", "2.3",
    "3.1", "3.2", "3.3", "3.4",
    "4.1", "4.2", "4.3",
)
FLIGHTS = ("1", "2", "3", "4")

_ID_RE = re.compile(r"^[Qq]?(\d)(?:[._](\d))?$")


def query_id(stem: str) -> str:
    """query_2_1 -> '2.1'"""
    _, flight, num = stem.split("_")
    return f"{int(flight)}.{int(num)}"


def query_label(stem: str) -> str:
    return f"Q{query_id(stem)}"


def load_queries(workload: str, filter_ids: list[str] | None) -> list[tuple[str, str]]:
    workload_dir = QUERIES_DIR / workload
    if not workload_dir.is_dir():
        raise RuntimeError(f"Workload directory not found: {workload_dir}")
    files = sorted(workload_dir.glob("query_*.sql"))
    queries: list[tuple[str, str]] = []
    for path in files:
        stem = path.stem  # e.g. query_2_1
        if filter_ids and query_id(stem) not in filter_ids:
            continue
        sql = path.read_text().strip().rstrip(";")
        queries.append((stem, sql))
    return queries


def _expand_id(raw: str) -> list[str] | None:
    """'2.1' / 'Q2.1' / '2_1' -> ['2.1']; a bare flight '3' -> ['3.1', ..., '3.4']."""
    m = _ID_RE.match(raw.strip())
    if not m:
        return None
    flight, num = m.groups()
    if num is None:
        return [q for q in QUERY_IDS if q.startswith(f"{flight}.")] or None
    qid = f"{flight}.{num}"
    return [qid] if qid in QUERY_IDS else None


def parse_query_filter(args) -> tuple[list[str] | None, int | None]:
    """Parse --query / --queries args. Returns (filter_ids, error_code)."""
    valid = ", ".join(QUERY_IDS)
    if args.query is not None and args.queries:
        print("Use either --query or --queries, not both.", file=sys.stderr)
        return None, 2
    if args.query is not None:
        m = _ID_RE.match(args.query.strip())
        ids = _expand_id(args.query) if m and m.group(2) is not None else None
        if not ids:
            print(f"Query must be one of {valid} (got {args.query!r}).", file=sys.stderr)
            return None, 2
        return ids, None
    if args.queries:
        ids: list[str] = []
        for raw in (x for x in args.queries.split(",") if x.strip()):
            expanded = _expand_id(raw)
            if not expanded:
                print(
                    f"Invalid --queries entry {raw!r}. Use comma-separated query ids "
                    f"({valid}) or whole flights (1-4), e.g. 1.1,2.3,4.",
                    file=sys.stderr,
                )
                return None, 2
            ids.extend(q for q in expanded if q not in ids)
        return ids, None
    return None, None
