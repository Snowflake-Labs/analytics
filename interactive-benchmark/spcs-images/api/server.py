"""Benchmark API server — runs queries against an interactive warehouse."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Semaphore
from typing import Any

import snowflake.connector
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from snowflake.connector import DictCursor

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

SOLUTION_NAME = os.environ.get("SOLUTION_NAME", "IW_TPCH")
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", f"{SOLUTION_NAME}_DB")
CONNECTION_NAME = os.environ.get("CONNECTION_NAME")
INTERACTIVE_WAREHOUSE = os.environ.get("INTERACTIVE_WAREHOUSE", f"{SOLUTION_NAME}_INT_WH")
INTERACTIVE_SCHEMA = os.environ.get("INTERACTIVE_SCHEMA", f"{SOLUTION_NAME}_IT")
QUERY_TAG = os.environ.get("QUERY_TAG", SOLUTION_NAME)
POOL_SIZE = int(os.environ.get("POOL_SIZE", "40"))
POOL_WARMUP = int(os.environ.get("POOL_WARMUP", "0"))
POOL_ACQUIRE_TIMEOUT = float(os.environ.get("POOL_ACQUIRE_TIMEOUT", "30"))
QUERIES_DIR = os.environ.get(
    "BENCHMARK_QUERIES_DIR",
    str(ROOT_DIR / "test") if not Path("/app/test").exists() else "/app/test",
)
WORKLOAD_FILE = os.environ.get("BENCHMARK_WORKLOAD_FILE")
WORKERS = int(os.environ.get("WORKERS", "1"))
PORT = int(os.environ.get("PORT", "3000"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("benchmark")


def base_connection_kwargs() -> dict[str, Any]:
    if CONNECTION_NAME:
        os.environ["SNOWFLAKE_DEFAULT_CONNECTION_NAME"] = CONNECTION_NAME
        return {"connection_name": CONNECTION_NAME}
    return {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ.get("SNOWFLAKE_USER"),
        "role": os.environ.get("SNOWFLAKE_ROLE", "PUBLIC"),
        "authenticator": os.environ.get("SNOWFLAKE_AUTHENTICATOR", "externalbrowser"),
    }


Context = tuple[str, str]
DEFAULT_CONTEXT: Context = (DATABASE, INTERACTIVE_SCHEMA)


@dataclass(frozen=True)
class Query:
    id: str
    sql: str
    weight: float
    context: Context


def connection_kwargs(context: Context = DEFAULT_CONTEXT) -> dict[str, Any]:
    database, schema = context
    return {
        **base_connection_kwargs(),
        "warehouse": INTERACTIVE_WAREHOUSE,
        "database": database,
        "schema": schema,
        "session_parameters": {
            "QUERY_TAG": QUERY_TAG,
            "USE_CACHED_RESULT": False,
        },
    }


class ConnectionPool:
    """Bounded, blocking pool: at most POOL_SIZE live connections.

    - A Semaphore caps total live connections (idle + borrowed) across contexts.
    - Idle connections are kept in one unbounded Queue per (database, schema),
      so a connection is only reused for queries in its own context.
    - acquire() blocks up to POOL_ACQUIRE_TIMEOUT waiting for a slot; if the
      idle queue is empty when a slot is granted, a new connection is created.
    - release() returns the connection to the idle queue and frees the slot.
    """

    def __init__(self, size: int = POOL_SIZE) -> None:
        self._size = size
        self._idle: dict[Context, Queue[snowflake.connector.SnowflakeConnection]] = {}
        self._sem = Semaphore(size)

    def _queue(self, context: Context) -> Queue[snowflake.connector.SnowflakeConnection]:
        return self._idle.setdefault(context, Queue())

    def _new_connection(self, context: Context) -> snowflake.connector.SnowflakeConnection:
        return snowflake.connector.connect(**connection_kwargs(context))

    def acquire(self, context: Context = DEFAULT_CONTEXT) -> snowflake.connector.SnowflakeConnection:
        if not self._sem.acquire(timeout=POOL_ACQUIRE_TIMEOUT):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Connection pool exhausted "
                    f"(size={self._size}, timeout={POOL_ACQUIRE_TIMEOUT}s)"
                ),
            )
        try:
            while True:
                try:
                    conn = self._queue(context).get_nowait()
                except Empty:
                    return self._new_connection(context)
                if conn.is_closed():
                    continue
                return conn
        except Exception:
            self._sem.release()
            raise

    def release(
        self, conn: snowflake.connector.SnowflakeConnection, context: Context = DEFAULT_CONTEXT
    ) -> None:
        try:
            if not conn.is_closed():
                self._queue(context).put_nowait(conn)
        finally:
            self._sem.release()

    def warmup(self, count: int | None = None, contexts: list[Context] | None = None) -> int:
        """Pre-open up to `count` (default: POOL_SIZE) connections and park them.

        Connections are spread round-robin over `contexts` (default: the default
        context). Acquires a semaphore slot for each connection so the pool
        invariant (at most ``size`` live connections) is preserved.
        """
        want = self._size if count is None else min(count, self._size)
        contexts = contexts or [DEFAULT_CONTEXT]
        opened = 0
        for i in range(want):
            context = contexts[i % len(contexts)]
            if not self._sem.acquire(timeout=0):
                break
            try:
                conn = self._new_connection(context)
            except Exception as exc:
                self._sem.release()
                raise RuntimeError(
                    f"Pool warmup failed after {opened}/{want}: {exc}"
                ) from exc
            self._queue(context).put_nowait(conn)
            opened += 1
        return opened

    def close_all(self) -> None:
        """Close all idle connections in the pool."""
        for queue in list(self._idle.values()):
            while True:
                try:
                    conn = queue.get_nowait()
                except Empty:
                    break
                try:
                    conn.close()
                except Exception:
                    pass


pool = ConnectionPool()
pool_ready = asyncio.Event()
pool_warmup_error: str | None = None


def load_query_registry(directory: str) -> dict[str, Query]:
    """Load .sql files from directory, equally weighted, in the default context."""
    queries_path = Path(directory)
    registry: dict[str, Query] = {}
    if not queries_path.exists():
        log.warning("Queries directory does not exist: %s", directory)
        return registry
    for sql_file in sorted(queries_path.glob("*.sql")):
        text = sql_file.read_text().strip()
        if text:
            registry[sql_file.stem] = Query(sql_file.stem, text, 1.0, DEFAULT_CONTEXT)
    log.info("Loaded %d queries from %s", len(registry), directory)
    return registry


def load_workload_file(path: str) -> dict[str, Query]:
    """Load a workload manifest: {"queries": [{id, sql, weight, database, schema}]}."""
    entries = json.loads(Path(path).read_text())["queries"]
    if not entries:
        raise ValueError(f"Workload file {path} has no queries")
    registry: dict[str, Query] = {}
    for entry in entries:
        query = Query(
            id=str(entry["id"]),
            sql=str(entry["sql"]).strip(),
            weight=float(entry["weight"]),
            context=(str(entry["database"]), str(entry["schema"])),
        )
        if not query.sql:
            raise ValueError(f"Query {query.id} in {path} has empty SQL")
        if query.weight <= 0:
            raise ValueError(f"Query {query.id} in {path} has non-positive weight {query.weight}")
        if query.id in registry:
            raise ValueError(f"Duplicate query id {query.id} in {path}")
        registry[query.id] = query
    log.info("Loaded %d weighted queries from %s", len(registry), path)
    return registry


query_registry: dict[str, Query] = (
    load_workload_file(WORKLOAD_FILE) if WORKLOAD_FILE else load_query_registry(QUERIES_DIR)
)


def workload_contexts() -> list[Context]:
    return sorted({query.context for query in query_registry.values()})


def execute_query(query: Query) -> dict[str, Any]:
    conn = pool.acquire(query.context)
    try:
        with conn.cursor(DictCursor) as cur:
            t0 = time.perf_counter()
            cur.execute(query.sql)
            elapsed_ms = round((time.perf_counter() - t0) * 1000)
            rows = cur.fetchall()
            return {
                "elapsed_ms": elapsed_ms,
                "row_count": len(rows),
                "warehouse": INTERACTIVE_WAREHOUSE,
                "query_id": cur.sfqid,
            }
    finally:
        pool.release(conn, query.context)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # asyncio.to_thread's default executor caps at min(32, cpu + 4) threads,
    # which would queue requests inside the API below POOL_SIZE.
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="query")
    )
    log.info("Benchmark API running at http://localhost:%s", PORT)
    log.info("Pool size: %d, workers: %d", POOL_SIZE, WORKERS)
    log.info("Database: %s", DATABASE)
    log.info("Warehouse: %s", INTERACTIVE_WAREHOUSE)
    log.info("Schema: %s", INTERACTIVE_SCHEMA)
    if CONNECTION_NAME:
        log.info("Snowflake connection: %s", CONNECTION_NAME)

    if POOL_WARMUP > 0:
        async def _warmup() -> None:
            global pool_warmup_error
            try:
                opened = await asyncio.to_thread(pool.warmup, POOL_WARMUP, workload_contexts())
            except Exception as exc:  # noqa: BLE001
                pool_warmup_error = str(exc)
                log.exception("Connection pool warmup failed: %s", exc)
                return

            expected = min(POOL_WARMUP, POOL_SIZE)
            if opened != expected:
                pool_warmup_error = (
                    f"Connection pool warmup opened {opened}/{expected} connections"
                )
                log.error(pool_warmup_error)
                return

            log.info("Prewarmed %d/%d connections", opened, expected)
            pool_ready.set()

        warmup_task = asyncio.create_task(_warmup())
    else:
        pool_ready.set()
        warmup_task = None
    yield
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()
    pool.close_all()


app = FastAPI(title="Interactive Warehouse Benchmark API", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})
    log.exception("Unhandled exception on %s %s", _request.method, _request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


class RunRequest(BaseModel):
    query_id: str


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ready")
async def ready() -> dict[str, str]:
    if pool_warmup_error is not None:
        raise HTTPException(
            status_code=503,
            detail="Connection pool warmup failed; inspect API logs",
        )
    if not pool_ready.is_set():
        raise HTTPException(status_code=503, detail="Pool warming up")
    return {"status": "ready"}


@app.get("/api/queries")
async def list_queries() -> list[str]:
    return sorted(query_registry.keys())


@app.get("/api/workload")
async def workload() -> list[dict[str, Any]]:
    return [{"id": q.id, "weight": q.weight} for q in sorted(query_registry.values(), key=lambda q: q.id)]


@app.post("/api/run")
@app.post("/api/run/interactive")
async def run_query(body: RunRequest) -> dict[str, Any]:
    query = query_registry.get(body.query_id)
    if query is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown query_id '{body.query_id}'. Use GET /api/queries to list available IDs.",
        )
    return await asyncio.to_thread(execute_query, query)


@app.post("/api/run/baseline")
async def run_baseline(body: RunRequest) -> dict[str, Any]:
    """No-op endpoint for infrastructure baseline testing."""
    return {
        "elapsed_ms": 0,
        "row_count": 0,
        "warehouse": None,
        "query_id": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark API server")
    parser.add_argument("--port", type=int, default=PORT, help="HTTP port")
    parser.add_argument(
        "--workers",
        type=int,
        default=WORKERS,
        help="Number of Uvicorn worker processes (default: WORKERS env or 1)",
    )
    args, _unknown = parser.parse_known_args()

    port = args.port
    workers = max(1, args.workers)

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        workers=workers if workers > 1 else None,
    )


if __name__ == "__main__":
    main()
