"""Thin wrapper over a Snowflake connection, so tests can substitute a fake."""

from __future__ import annotations

import os
from typing import Any, Protocol

import snowflake.connector
from snowflake.connector import DictCursor

STATEMENT_TIMEOUT = 630  # 000630: includes the interactive warehouse's 5 s limit


class Snow(Protocol):
    def rows(self, sql: str, params: tuple | None = None) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: tuple | None = None) -> str: ...

    def close(self) -> None: ...


class ConnectorSnow:
    def __init__(self, conn: snowflake.connector.SnowflakeConnection) -> None:
        self._conn = conn

    def rows(self, sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
        with self._conn.cursor(DictCursor) as cur:
            cur.execute(sql, params)
            return [{k.upper(): v for k, v in row.items()} for row in cur.fetchall()]

    def execute(self, sql: str, params: tuple | None = None) -> str:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            cur.fetchall()
            return cur.sfqid

    def close(self) -> None:
        self._conn.close()


def connect(connection_name: str) -> ConnectorSnow:
    """Open the named connection (in a sandbox: the platform-rendered `default`).

    SNOWFLAKE_ROLE, set by the sandbox platform, overrides the connection's role.
    """
    role = os.environ.get("SNOWFLAKE_ROLE")
    conn = snowflake.connector.connect(
        connection_name=connection_name,
        **({"role": role} if role else {}),
        session_parameters={"USE_CACHED_RESULT": False},
    )
    return ConnectorSnow(conn)
