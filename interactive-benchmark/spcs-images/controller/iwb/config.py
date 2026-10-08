"""Load and validate the run config (iwb-config.schema.json plus checks JSON Schema cannot express)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from iwb import budget

SCHEMA_PATH = Path(__file__).with_name("iwb-config.schema.json")
WEIGHT_TOLERANCE = 0.01


class ConfigError(Exception):
    """Invalid input, missing access, or anything the user must fix."""


@dataclass(frozen=True)
class Context:
    database: str
    schema: str


@dataclass(frozen=True)
class SqlEntry:
    sql: str
    weight_pct: float
    context: Context


@dataclass(frozen=True)
class QueryIdEntry:
    query_id: str
    weight_pct: float | None


@dataclass(frozen=True)
class NewWarehouse:
    size: str
    min_cluster_count: int
    max_cluster_count: int


@dataclass(frozen=True)
class Config:
    context: Context
    sql_entries: tuple[SqlEntry, ...]
    query_id_entries: tuple[QueryIdEntry, ...]
    existing_warehouse: str | None
    new_warehouse: NewWarehouse | None
    fallback_warehouse: str | None
    lookup_warehouse: str | None
    concurrent_users: int
    run_minutes: float

    @property
    def run_seconds(self) -> int:
        return budget.run_seconds(self.run_minutes)


def _context(raw: dict) -> Context:
    return Context(raw["database"], raw["schema"])


def _check_weights(weights: list[float], what: str) -> None:
    total = sum(weights)
    if abs(total - 100) > WEIGHT_TOLERANCE:
        raise ConfigError(f"{what} weight_pct values must sum to 100, got {total:g}")


def parse(raw: dict) -> Config:
    validator = jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors)
        raise ConfigError(f"Invalid config: {details}")

    context = _context(raw["context"])
    workload = raw["workload"]
    sql_entries: list[SqlEntry] = []
    query_id_entries: list[QueryIdEntry] = []
    if "queries" in workload:
        sql_entries = [
            SqlEntry(q["sql"], float(q["weight_pct"]), _context(q["context"]) if "context" in q else context)
            for q in workload["queries"]
        ]
        _check_weights([e.weight_pct for e in sql_entries], "queries")
    else:
        for item in workload["query_ids"]:
            if isinstance(item, str):
                query_id_entries.append(QueryIdEntry(item, None))
            else:
                query_id_entries.append(QueryIdEntry(item["query_id"], item.get("weight_pct")))
        weighted = [e.weight_pct for e in query_id_entries if e.weight_pct is not None]
        if weighted and len(weighted) != len(query_id_entries):
            raise ConfigError("query_ids: give weight_pct for every query ID or for none")
        if weighted:
            _check_weights(weighted, "query_ids")
    if sql_entries and "lookup_warehouse" in raw:
        raise ConfigError("lookup_warehouse is only used with workload.query_ids")

    warehouse = raw["warehouse"]
    new_warehouse = None
    if "create" in warehouse:
        create = warehouse["create"]
        new_warehouse = NewWarehouse(
            create["size"], create.get("min_cluster_count", 1), create.get("max_cluster_count", 1)
        )
        if new_warehouse.min_cluster_count > new_warehouse.max_cluster_count:
            raise ConfigError("warehouse.create: min_cluster_count must not exceed max_cluster_count")

    return Config(
        context=context,
        sql_entries=tuple(sql_entries),
        query_id_entries=tuple(query_id_entries),
        existing_warehouse=warehouse.get("existing"),
        new_warehouse=new_warehouse,
        fallback_warehouse=raw.get("fallback_warehouse"),
        lookup_warehouse=raw.get("lookup_warehouse"),
        concurrent_users=raw["concurrent_users"],
        run_minutes=float(raw["run_minutes"]),
    )


def load(path: str | Path) -> Config:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read config {path}: {exc}") from exc
    return parse(raw)
