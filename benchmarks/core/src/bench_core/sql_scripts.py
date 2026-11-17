from __future__ import annotations

import re
from pathlib import Path

from bench_core import config as _cfg


def split_statements(sql_text: str) -> list[str]:
    """Split a script into individual statements.

    Strips line comments and trailing semicolons. Good enough for our DDL files,
    which contain only line comments and statements separated by `;`.
    """
    cleaned = re.sub(r"--[^\n]*", "", sql_text)
    parts = [p.strip() for p in cleaned.split(";")]
    return [p for p in parts if p]


def execute_script(conn, script_path: Path, substitutions: dict[str, str] | None = None) -> None:
    text = script_path.read_text()
    for token, value in (substitutions or {}).items():
        text = text.replace(token, value)
    statements = split_statements(text)
    cur = conn.cursor()
    try:
        for stmt in statements:
            preview = " ".join(stmt.split())[:120]
            print(f"  > {preview}")
            cur.execute(stmt)
    finally:
        cur.close()


def print_setup_tables(conn, scale: str) -> None:
    std_schema = _cfg.schema_for_scale(scale)
    it_schema = _cfg.interactive_schema_for_scale(scale)
    ice_schema = _cfg.iceberg_schema_for_scale(scale)
    sql = (
        f"SELECT TABLE_SCHEMA || '.' || TABLE_NAME AS table_name, "
        f"CLUSTERING_KEY, ROW_COUNT "
        f"FROM {_cfg.BENCHMARK_DATABASE}.INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_SCHEMA IN ('{std_schema}', '{it_schema}', '{ice_schema}') "
        f"ORDER BY TABLE_SCHEMA, TABLE_NAME"
    )
    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        cur.close()

    print("\nTables:")
    print(f"{'table':<40}  {'clustering_key':<30}  {'rows':>12}")
    print("-" * 86)
    for table_name, clustering_key, row_count in rows:
        rows_s = "" if row_count is None else f"{row_count:,}"
        print(f"{table_name:<40}  {clustering_key or '':<30}  {rows_s:>12}")
