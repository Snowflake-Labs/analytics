from __future__ import annotations

import sys

from bench_core import config as _cfg


def query_id(stem: str) -> str:
    return _cfg.SPEC.stem_to_id(stem)


def query_label(stem: str) -> str:
    return _cfg.SPEC.query_label(stem)


def load_queries(workload: str, filter_ids: list[str] | None) -> list[tuple[str, str]]:
    workload_dir = _cfg.QUERIES_DIR / workload
    if not workload_dir.is_dir():
        raise RuntimeError(f"Workload directory not found: {workload_dir}")
    files = sorted(workload_dir.glob("query_*.sql"))
    queries: list[tuple[str, str]] = []
    for path in files:
        stem = path.stem  # e.g. query_01 / query_2_1
        if filter_ids and query_id(stem) not in filter_ids:
            continue
        sql = path.read_text().strip().rstrip(";")
        queries.append((stem, sql))
    return queries


def _valid_ids() -> str:
    ids = _cfg.SPEC.query_ids
    return f"{ids[0]}–{ids[-1]}"


def parse_query_filter(args) -> tuple[list[str] | None, int | None]:
    """Parse --query / --queries args. Returns (filter_ids, error_code)."""
    spec = _cfg.SPEC
    if args.query is not None and args.queries:
        print("Use either --query or --queries, not both.", file=sys.stderr)
        return None, 2
    if args.query is not None:
        ids = spec.expand(str(args.query))
        if not ids or len(ids) != 1:
            print(f"Query must be one of {_valid_ids()} (got {args.query!r}).", file=sys.stderr)
            return None, 2
        return ids, None
    if args.queries:
        ids: list[str] = []
        for raw in (x for x in args.queries.split(",") if x.strip()):
            expanded = spec.expand(raw)
            if not expanded:
                print(
                    f"Invalid --queries entry {raw!r}. {spec.queries_help}.",
                    file=sys.stderr,
                )
                return None, 2
            ids.extend(q for q in expanded if q not in ids)
        return ids, None
    return None, None
