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


def _monitored(snow: Snow, path: str) -> list[dict]:
    response = snow.monitoring(path)
    data = response.get("data")
    if not response.get("success") or not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise RuntimeError(f"Unexpected response from {path}: {str(response)[:300]}")
    return data["queries"]


def _from_monitoring(snow: Snow, query_id: str) -> dict | None:
    """The query as a history row, or None if this user cannot see it (job retention, 14 days by default).

    The monitoring API has no statement type, but filters on it: ask for the SELECT first.
    """
    found = _monitored(snow, f"/monitoring/queries?uuid={query_id}&stmt_type=SELECT&max=1")
    query_type = "SELECT"
    if not found:
        found = _monitored(snow, f"/monitoring/queries/{query_id}")
        query_type = None
    if not found:
        return None
    q = found[0]
    return {"QUERY_ID": query_id, "QUERY_TEXT": q.get("sqlText"), "DATABASE_NAME": q.get("databaseName"),
            "SCHEMA_NAME": q.get("schemaName"), "QUERY_TYPE": query_type, "EXECUTION_STATUS": q.get("status")}


def _from_account_usage(snow: Snow, query_ids: list[str], lookup_warehouse: str) -> list[dict]:
    try:
        snow.execute(f"USE WAREHOUSE {ident(lookup_warehouse)}")
        return snow.rows(
            f"SELECT {HISTORY_COLUMNS} FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY "
            f"WHERE QUERY_ID IN ({', '.join(['%s'] * len(query_ids))})",
            tuple(query_ids),
        )
    except ProgrammingError as exc:
        raise ConfigError(f"lookup_warehouse is set, but ACCOUNT_USAGE cannot be read with it: {exc.msg}") from exc


def _history(snow: Snow, query_ids: list[str], lookup_warehouse: str | None) -> dict[str, dict]:
    """Look up query IDs through the GS monitoring API (no warehouse needed), then, for IDs past
    job retention, in ACCOUNT_USAGE (365 days) if a lookup warehouse is configured."""
    found = {qid: row for qid in query_ids if (row := _from_monitoring(snow, qid))}
    missing = [qid for qid in query_ids if qid not in found]
    if missing and lookup_warehouse:
        found.update({row["QUERY_ID"]: row for row in _from_account_usage(snow, missing, lookup_warehouse)})
    return found


def _from_query_ids(cfg: Config, snow: Snow) -> list[Query]:
    ids = [e.query_id for e in cfg.query_id_entries]
    history = _history(snow, sorted(set(ids)), cfg.lookup_warehouse)
    missing = sorted(set(ids) - history.keys())
    if missing:
        raise ConfigError(
            "Query IDs not found in query history visible to this user (own queries, or MONITOR on the "
            "user or warehouse; last 14 days by default; set lookup_warehouse to also search "
            f"ACCOUNT_USAGE, 365 days): {missing}"
        )
    equal = 100 / len(cfg.query_id_entries)
    queries = []
    for i, entry in enumerate(cfg.query_id_entries, 1):
        row = history[entry.query_id]
        if row["QUERY_TYPE"] != "SELECT":
            kind = f" ({row['QUERY_TYPE']})" if row["QUERY_TYPE"] else ""
            raise ConfigError(f"Query {entry.query_id} is not a SELECT{kind}")
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
