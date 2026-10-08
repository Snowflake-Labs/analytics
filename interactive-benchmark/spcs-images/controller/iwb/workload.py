"""Turn config workload entries into runnable queries and find the tables they read."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from snowflake.connector.errors import ProgrammingError

from iwb.config import Config, ConfigError, Context
from iwb.snow import Snow

PLAIN_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
HISTORY_COLUMNS = "QUERY_ID, QUERY_TEXT, DATABASE_NAME, SCHEMA_NAME, QUERY_TYPE, EXECUTION_STATUS"
NOT_FOUND_OR_UNAUTHORIZED = 2003
NO_ACTIVE_WAREHOUSE = 606
NEEDS_WAREHOUSE = (
    "workload.query_ids needs a warehouse to read query history: set lookup_warehouse in the config "
    "(a standard warehouse this role can use), or a DEFAULT_WAREHOUSE on your user"
)


def ident(name: str) -> str:
    """Render one identifier part; plain names stay unquoted (case-insensitive)."""
    if PLAIN_IDENTIFIER.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def qualified(context: Context) -> str:
    return f"{ident(context.database)}.{ident(context.schema)}"


@dataclass(frozen=True)
class Query:
    id: str
    sql: str
    weight: float
    context: Context
    source: str


def _history(snow: Snow, query_ids: list[str], context: Context, lookup_warehouse: str | None) -> dict[str, dict]:
    """Look up query IDs in ACCOUNT_USAGE (365 days), then INFORMATION_SCHEMA (7 days, fresh).

    Both need a running warehouse, and an interactive one would hit its 5 s timeout on these scans.
    """
    if lookup_warehouse:
        try:
            snow.execute(f"USE WAREHOUSE {ident(lookup_warehouse)}")
        except ProgrammingError as exc:
            raise ConfigError(f"lookup_warehouse {lookup_warehouse} cannot be used: {exc.msg}") from exc
    try:
        return _lookup(snow, query_ids, context)
    except ProgrammingError as exc:
        if exc.errno == NO_ACTIVE_WAREHOUSE:
            raise ConfigError(NEEDS_WAREHOUSE) from exc
        raise


def _lookup(snow: Snow, query_ids: list[str], context: Context) -> dict[str, dict]:
    found: dict[str, dict] = {}
    placeholders = ", ".join(["%s"] * len(query_ids))
    try:
        for row in snow.rows(
            f"SELECT {HISTORY_COLUMNS} FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY "
            f"WHERE QUERY_ID IN ({placeholders})",
            tuple(query_ids),
        ):
            found[row["QUERY_ID"]] = row
    except ProgrammingError as exc:
        if exc.errno != NOT_FOUND_OR_UNAUTHORIZED:
            raise
        # No SNOWFLAKE database access; INFORMATION_SCHEMA still covers recent queries.
    missing = [q for q in query_ids if q not in found]
    if missing:
        placeholders = ", ".join(["%s"] * len(missing))
        for row in snow.rows(
            f"SELECT {HISTORY_COLUMNS} FROM TABLE({ident(context.database)}.INFORMATION_SCHEMA"
            f".QUERY_HISTORY_BY_USER(RESULT_LIMIT => 10000)) WHERE QUERY_ID IN ({placeholders})",
            tuple(missing),
        ):
            found[row["QUERY_ID"]] = row
    return found


def _from_query_ids(cfg: Config, snow: Snow) -> list[Query]:
    ids = [e.query_id for e in cfg.query_id_entries]
    history = _history(snow, sorted(set(ids)), cfg.context, cfg.lookup_warehouse)
    missing = sorted(set(ids) - history.keys())
    if missing:
        raise ConfigError(
            "Query IDs not found in query history visible to this role "
            f"(ACCOUNT_USAGE: 365 days, INFORMATION_SCHEMA: last 7 days, own queries): {missing}"
        )
    equal = 100 / len(cfg.query_id_entries)
    queries = []
    for i, entry in enumerate(cfg.query_id_entries, 1):
        row = history[entry.query_id]
        if row["QUERY_TYPE"] != "SELECT":
            raise ConfigError(f"Query {entry.query_id} is a {row['QUERY_TYPE']}, not a SELECT")
        if row["EXECUTION_STATUS"] != "SUCCESS":
            raise ConfigError(f"Query {entry.query_id} did not succeed ({row['EXECUTION_STATUS']})")
        if not row["QUERY_TEXT"]:
            raise ConfigError(f"Query {entry.query_id} has no query text visible to this role")
        context = (
            Context(row["DATABASE_NAME"], row["SCHEMA_NAME"])
            if row["DATABASE_NAME"] and row["SCHEMA_NAME"]
            else cfg.context
        )
        weight = entry.weight_pct if entry.weight_pct is not None else equal
        queries.append(Query(f"q{i:02d}", row["QUERY_TEXT"], weight, context, entry.query_id))
    return queries


def _merge_duplicates(queries: list[Query]) -> list[Query]:
    merged: dict[tuple[str, Context], Query] = {}
    for q in queries:
        key = (q.sql.strip(), q.context)
        if key in merged:
            prev = merged[key]
            merged[key] = Query(prev.id, prev.sql, prev.weight + q.weight, prev.context, prev.source)
        else:
            merged[key] = q
    return list(merged.values())


def resolve(cfg: Config, snow: Snow) -> list[Query]:
    if cfg.sql_entries:
        queries = [
            Query(f"q{i:02d}", e.sql, e.weight_pct, e.context, "sql")
            for i, e in enumerate(cfg.sql_entries, 1)
        ]
    else:
        queries = _from_query_ids(cfg, snow)
    return _merge_duplicates(queries)


def use_context(snow: Snow, context: Context) -> None:
    snow.execute(f"USE SCHEMA {qualified(context)}")


def explain_plan(snow: Snow, q: Query) -> dict:
    use_context(snow, q.context)
    return json.loads(snow.rows("SELECT SYSTEM$EXPLAIN_PLAN_JSON(%s) AS PLAN", (q.sql,))[0]["PLAN"])


def discover_tables(snow: Snow, queries: list[Query]) -> set[str]:
    """Compile every query as the caller (proving access) and collect the tables its plan scans.

    Tables the optimizer prunes are never read, so they need no caching. Interactive tables must
    still be attached; Warehouse.ensure_compiles catches those.
    """
    tables: set[str] = set()
    for q in queries:
        try:
            plan = explain_plan(snow, q)
        except ProgrammingError as exc:
            raise ConfigError(f"Query {q.id} ({q.source}) does not compile for this role: {exc.msg}") from exc
        for step in plan.get("Operations", []):
            for operation in step:
                tables.update(operation.get("objects", []))
    return tables
