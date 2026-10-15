"""Thin wrapper over a Snowflake connection, so tests can substitute a fake."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

import snowflake.connector
from snowflake.connector import DictCursor

STATEMENT_TIMEOUT = 630  # 000630: includes the interactive warehouse's 5 s limit
SESSION_TOKEN_FILE = "/snowflake/session/token"


class Snow(Protocol):
    def rows(self, sql: str, params: tuple | None = None) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: tuple | None = None) -> str: ...

    def monitoring(self, path: str) -> dict[str, Any]: ...

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

    def monitoring(self, path: str) -> dict[str, Any]:
        """GET a GS monitoring endpoint (as get_query_status() does); needs no warehouse."""
        return self._conn.rest.request(path, method="get", client="rest")

    def close(self) -> None:
        self._conn.close()


def session_token_connection(connection_name: str) -> Path | None:
    """Render `connection_name` from the session token of a sandbox or SPCS container, if there is one.

    Sets SNOWFLAKE_HOME so the API server, a child process, connects the same way. Preferred over a
    platform-rendered connections.toml, as in the sandbox SDK: a sandbox on a non-default image has
    none, and the default image's uses the hyphenated account host, which the egress proxy rejects
    (403) for account names with underscores.
    """
    token_file = os.environ.get("SNOWFLAKE_TOKEN_FILE_PATH", SESSION_TOKEN_FILE)
    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    host = os.environ.get("SNOWFLAKE_HOST") or os.environ.get("SNOWFLAKE_ACCOUNT_URL", "")
    host = host.split("://", 1)[-1].split("/", 1)[0]
    if not (account and host and os.access(token_file, os.R_OK)):
        return None
    home = Path(tempfile.mkdtemp(prefix="iwb-snowflake-"))
    path = home / "connections.toml"
    path.write_text(f'[{connection_name}]\naccount = "{account}"\nhost = "{host}"\n'
                    f'authenticator = "oauth"\ntoken_file_path = "{token_file}"\n')
    path.chmod(0o600)
    os.environ["SNOWFLAKE_HOME"] = str(home)
    return path


def connect(connection_name: str) -> ConnectorSnow:
    """Open the named connection, rendered from the session token when there is one.

    SNOWFLAKE_ROLE, set by the sandbox platform, overrides the connection's role.
    """
    role = os.environ.get("SNOWFLAKE_ROLE")
    # The connector read SNOWFLAKE_HOME when it was imported, so name the rendered file.
    rendered = session_token_connection(connection_name)
    conn = snowflake.connector.connect(
        connection_name=connection_name,
        **({"connections_file_path": rendered} if rendered else {}),
        **({"role": role} if role else {}),
        # The session idles from warm-up to measurement; a long run must not lose it before teardown.
        client_session_keep_alive=True,
        session_parameters={"USE_CACHED_RESULT": False},
    )
    return ConnectorSnow(conn)
