"""Verify an existing interactive warehouse, or create one for the run and drop it afterwards."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from snowflake.connector.errors import ProgrammingError

from iwb.budget import STARTED_TIMEOUT_SECONDS
from iwb.config import Config, ConfigError, Context
from iwb.events import Events
from iwb.snow import Snow
from iwb.workload import Query, explain_plan, ident, qualified

TABLE_NOT_BOUND = 10402  # 010402: Interactive table {0} needs to be added to current warehouse.
NOT_BOUND_TABLE = re.compile(r"Interactive table (\S+) needs to be added")
STALE_PREFIX = "IWB_"
COMMENT_PREFIX = "iwb:"
MAX_ATTACH_ROUNDS = 100  # an interactive warehouse attaches at most 100 tables


@dataclass
class Warehouse:
    name: str
    created: bool
    tables: set[str] = field(default_factory=set)


def _show(snow: Snow, name: str) -> dict | None:
    rows = [r for r in snow.rows("SHOW WAREHOUSES LIKE %s", (name,)) if r["NAME"].upper() == name.upper()]
    return rows[0] if rows else None


def _attached(row: dict) -> set[str]:
    return {t.strip().upper() for t in (row.get("TABLES") or "").split(",") if t.strip()}


def cleanup_stale(snow: Snow, events: Events, now: float | None = None) -> None:
    """Drop warehouses left by runs that died, identified by an expired iwb comment.

    Best effort: a warehouse this role cannot drop is reported and skipped.
    """
    now = time.time() if now is None else now
    for row in snow.rows("SHOW WAREHOUSES LIKE %s", (STALE_PREFIX.replace("_", "\\_") + "%",)):
        comment = row.get("COMMENT") or ""
        match = re.fullmatch(rf"{COMMENT_PREFIX}(\S+):expires=(\d+)", comment)
        if match and int(match.group(2)) < now:
            try:
                snow.execute(f"DROP WAREHOUSE IF EXISTS {ident(row['NAME'])}")
            except ProgrammingError as exc:
                events.emit("CLEANUP", "warning", warehouse=row["NAME"], error=exc.msg)
                continue
            events.emit("CLEANUP", "completed", dropped=row["NAME"], stale_run_id=match.group(1))


def prepare(cfg: Config, snow: Snow, tables: set[str], run_id: str, events: Events) -> Warehouse:
    """Verify the existing warehouse, or create one. Returns as soon as a created warehouse
    exists, so the caller can always drop it; activate() does everything that can fail after."""
    if cfg.existing_warehouse:
        row = _show(snow, cfg.existing_warehouse)
        if row is None:
            raise ConfigError(f"Warehouse {cfg.existing_warehouse} does not exist or is not visible to this role")
        if row.get("TYPE") != "INTERACTIVE":
            raise ConfigError(f"Warehouse {cfg.existing_warehouse} is {row.get('TYPE')}, not INTERACTIVE")
        not_attached = sorted(t for t in tables if t.upper() not in _attached(row))
        if not_attached:
            events.emit("SETUP_WAREHOUSE", "warning", reason="tables not attached; reads are not cached",
                        tables=not_attached)
        return Warehouse(row["NAME"], created=False, tables=_attached(row))
    new = cfg.new_warehouse
    assert new is not None
    cleanup_stale(snow, events)
    name = f"{STALE_PREFIX}{run_id}_IW"
    expires = int(time.time()) + cfg.run_seconds + 3 * 3600
    tables_clause = f"TABLES ({', '.join(sorted(tables))}) " if tables else ""
    snow.execute(
        f"CREATE INTERACTIVE WAREHOUSE {ident(name)} {tables_clause}"
        f"WAREHOUSE_SIZE = '{new.size}' MIN_CLUSTER_COUNT = {new.min_cluster_count} "
        f"MAX_CLUSTER_COUNT = {new.max_cluster_count} AUTO_SUSPEND = 86400 AUTO_RESUME = TRUE "
        f"COMMENT = '{COMMENT_PREFIX}{run_id}:expires={expires}'"
    )
    events.emit("SETUP_WAREHOUSE", "created", warehouse=name, tables=sorted(tables))
    return Warehouse(name, created=True, tables={t.upper() for t in tables})


def activate(cfg: Config, snow: Snow, wh: Warehouse) -> None:
    snow.execute(f"ALTER WAREHOUSE {ident(wh.name)} RESUME IF SUSPENDED")
    if cfg.fallback_warehouse and wh.created:
        snow.execute(f"ALTER WAREHOUSE {ident(wh.name)} SET FALLBACK_WAREHOUSE = {ident(cfg.fallback_warehouse)}")
    wait_started(snow, wh.name)


def wait_started(snow: Snow, name: str, timeout: float = STARTED_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        row = _show(snow, name)
        state = row and row.get("STATE")
        if state == "STARTED":
            return
        time.sleep(5)
    raise RuntimeError(f"Warehouse {name} did not reach STARTED within {timeout:.0f}s (last state {state})")


def _qualify(name: str, context: Context) -> str:
    parts = name.split(".")
    if len(parts) == 3:
        return name
    if len(parts) == 2:
        return f"{ident(context.database)}.{name}"
    return f"{qualified(context)}.{name}"


def ensure_compiles(snow: Snow, wh: Warehouse, queries: list[Query], events: Events) -> None:
    """Compile each query on the interactive warehouse; attach any interactive table it names."""
    snow.execute(f"USE WAREHOUSE {ident(wh.name)}")
    for q in queries:
        for _ in range(MAX_ATTACH_ROUNDS):
            try:
                explain_plan(snow, q)
                break
            except ProgrammingError as exc:
                match = NOT_BOUND_TABLE.search(exc.msg or "")
                if exc.errno != TABLE_NOT_BOUND or not match:
                    raise ConfigError(f"Query {q.id} ({q.source}) does not compile on {wh.name}: {exc.msg}") from exc
                table = _qualify(match.group(1), q.context)
                if not wh.created:
                    raise ConfigError(
                        f"Interactive table {table} used by query {q.id} is not attached to {wh.name}"
                    ) from exc
                snow.execute(f"ALTER WAREHOUSE {ident(wh.name)} ADD TABLES ({table})")
                wh.tables.add(table.upper())
                events.emit("SETUP_WAREHOUSE", "attached", warehouse=wh.name, table=table, query=q.id)
        else:
            raise ConfigError(f"Query {q.id}: gave up attaching tables after {MAX_ATTACH_ROUNDS} rounds")
    wait_started(snow, wh.name)


def drop(snow: Snow, wh: Warehouse, events: Events) -> None:
    if wh.created:
        snow.execute(f"DROP WAREHOUSE IF EXISTS {ident(wh.name)}")
        events.emit("TEARDOWN", "completed", dropped=wh.name)
