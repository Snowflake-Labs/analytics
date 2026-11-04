"""TPC-H benchmark dashboard API server (Python + Snowflake connector)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Semaphore
from typing import Any

import snowflake.connector
import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from snowflake.connector import DictCursor
from snowflake.connector.compat import IS_LINUX, IS_MACOS, IS_WINDOWS

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

SOLUTION_NAME = os.environ.get("SOLUTION_NAME", "IW_TPCH")
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", f"{SOLUTION_NAME}_BENCH_DB")
SCALES = ["1", "10", "100", "1000"]
CONNECTION_NAME = os.environ.get("CONNECTION_NAME")
POOL_SIZE = int(os.environ.get("POOL_SIZE", "40"))
POOL_WARMUP = int(os.environ.get("POOL_WARMUP", "0"))
POOL_ACQUIRE_TIMEOUT = float(os.environ.get("POOL_ACQUIRE_TIMEOUT", "30"))
WORKERS = int(os.environ.get("WORKERS", "1"))
LOOKBACK_DAYS_OPTIONS = [15, 30, 90, 180]
DEFAULT_LOOKBACK_DAYS = 90
TARGETS = ["standard", "interactive"]
DEFAULT_TARGET = "interactive"

QUERY_TAG = "IW_DEMO_DASHBOARD"
LOCUST_URL = os.environ.get("LOCUST_URL", "").rstrip("/")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dashboard")


def parse_cli_scale(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--scale" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.isdigit() and arg in SCALES:
            return arg
    return None


DEFAULT_SCALE = parse_cli_scale(sys.argv[1:]) or os.environ.get("DEFAULT_SCALE", "100")
PORT = int(os.environ.get("PORT", "3000"))
CREDENTIAL_CACHE_DIR = os.environ.get(
    "SNOWFLAKE_CREDENTIAL_CACHE_DIR",
    os.path.join(
        os.environ.get("SNOWFLAKE_HOME", os.path.join(Path.home(), ".snowflake")),
        "credential_cache",
    ),
)


def credential_cache_enabled() -> bool:
    return os.environ.get("SNOWFLAKE_CLIENT_STORE_TEMPORARY_CREDENTIAL", "true").lower() != "false"


def credential_cache_options() -> dict[str, Any]:
    if not credential_cache_enabled():
        return {"client_store_temporary_credential": False}
    return {"client_store_temporary_credential": True}


def credential_cache_description() -> str:
    if not credential_cache_enabled():
        return "disabled"
    if IS_MACOS or IS_WINDOWS:
        return "system keyring (secure-local-storage)"
    if IS_LINUX:
        cache_dir = os.environ.get(
            "SF_TEMPORARY_CREDENTIAL_CACHE_DIR",
            os.path.join(Path.home(), ".cache", "snowflake"),
        )
        return cache_dir
    return CREDENTIAL_CACHE_DIR


def schema_for_scale(scale: str) -> str:
    return f"TPCH_SF{scale}"


def warehouse_for_target(target: str, scale: str) -> str:
    return (
        f"{SOLUTION_NAME}_BENCH_WH_INT_{scale}"
        if target == "interactive"
        else f"{SOLUTION_NAME}_BENCH_WH_STD_{scale}"
    )


def resolve_target(raw: str | None) -> str:
    target = DEFAULT_TARGET if raw in (None, "") else str(raw)
    if target not in TARGETS:
        raise ValueError(
            f'Invalid warehouse target {raw!r}. Use "standard" or "interactive".'
        )
    return target


def resolve_scale(raw: str | None) -> str:
    scale = str(raw or DEFAULT_SCALE)
    if scale not in SCALES:
        raise ValueError(f"Invalid scale {scale}. Use one of: {', '.join(SCALES)}")
    return scale


def resolve_lookback(raw: str | int | None) -> int:
    if raw is None:
        return DEFAULT_LOOKBACK_DAYS
    try:
        val = int(raw)
    except (ValueError, TypeError):
        return DEFAULT_LOOKBACK_DAYS
    if val not in LOOKBACK_DAYS_OPTIONS:
        return DEFAULT_LOOKBACK_DAYS
    return val


def boundaries_cte(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> str:
    return f"""
WITH boundaries AS (
  SELECT
    MAX(L_SHIPDATE) AS end_date,
    DATEADD(day, -{lookback_days}, MAX(L_SHIPDATE)) AS start_date
  FROM LINEITEM_DASHBOARD
)"""


def ship_date_filter(alias: str = "l") -> str:
    return f"{alias}.L_SHIPDATE BETWEEN boundaries.start_date AND boundaries.end_date"


def lineitem_revenue(alias: str = "l") -> str:
    return f"{alias}.L_EXTENDEDPRICE * (1 - {alias}.L_DISCOUNT)"


def build_dashboard_query(
    *,
    select: str,
    segment: str | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    extra_where: list[str] | None = None,
    group_by: str = "",
    order_by: str = "",
    limit: str = "",
) -> tuple[str, list[Any]]:
    where = [ship_date_filter("l")]
    binds: list[Any] = []
    if segment:
        where.append("l.L_MKTSEGMENT = %s")
        binds.append(segment)
    if extra_where:
        where.extend(extra_where)

    parts = [
        boundaries_cte(lookback_days).strip(),
        select.strip(),
        "FROM LINEITEM_DASHBOARD l",
        "CROSS JOIN boundaries",
        f"WHERE {' AND '.join(where)}",
    ]
    if group_by:
        parts.append(group_by)
    if order_by:
        parts.append(order_by)
    if limit:
        parts.append(limit)

    return "\n".join(parts), binds


def serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def serialize_rows(data: Any) -> Any:
    if data is None:
        return None
    if isinstance(data, dict):
        return {k: serialize_value(v) for k, v in data.items()}
    if isinstance(data, list):
        if not data or not isinstance(data[0], dict):
            return [serialize_value(v) for v in data]
        return [{k: serialize_value(v) for k, v in row.items()} for row in data]
    return serialize_value(data)


def response_row_count(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    return 0 if data is None else 1


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


def connection_kwargs_for(target: str, scale: str) -> dict[str, Any]:
    return {
        **base_connection_kwargs(),
        **credential_cache_options(),
        "warehouse": warehouse_for_target(target, scale),
        "database": DATABASE,
        "schema": schema_for_scale(scale),
        "session_parameters": {
            "QUERY_TAG": QUERY_TAG,
            "USE_CACHED_RESULT": False,
        },
    }


class ConnectionPool:
    """Bounded, blocking pool: at most POOL_SIZE live connections per target/scale.

    - A per-key Semaphore caps total live connections (idle + borrowed).
    - Idle connections are kept in an unbounded Queue.
    - acquire() blocks up to POOL_ACQUIRE_TIMEOUT waiting for a slot; if the
      idle queue is empty when a slot is granted, a new connection is created.
    - release() returns the connection to the idle queue and frees the slot.
    """

    def __init__(self, size: int = POOL_SIZE) -> None:
        self._size = size
        self._idle: dict[str, Queue[snowflake.connector.SnowflakeConnection]] = {}
        self._sem: dict[str, Semaphore] = {}
        self._init_lock = Lock()

    def _slots_for(
        self, key: str
    ) -> tuple[Queue[snowflake.connector.SnowflakeConnection], Semaphore]:
        with self._init_lock:
            if key not in self._idle:
                self._idle[key] = Queue()
                self._sem[key] = Semaphore(self._size)
            return self._idle[key], self._sem[key]

    def _new_connection(self, target: str, scale: str) -> snowflake.connector.SnowflakeConnection:
        return snowflake.connector.connect(**connection_kwargs_for(target, scale))

    def acquire(self, target: str, scale: str) -> snowflake.connector.SnowflakeConnection:
        key = f"{target}:{scale}"
        idle, sem = self._slots_for(key)
        if not sem.acquire(timeout=POOL_ACQUIRE_TIMEOUT):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Connection pool exhausted for {key} "
                    f"(size={self._size}, timeout={POOL_ACQUIRE_TIMEOUT}s)"
                ),
            )
        try:
            while True:
                try:
                    conn = idle.get_nowait()
                except Empty:
                    return self._new_connection(target, scale)
                if conn.is_closed():
                    continue
                return conn
        except Exception:
            sem.release()
            raise

    def release(
        self, target: str, scale: str, conn: snowflake.connector.SnowflakeConnection
    ) -> None:
        key = f"{target}:{scale}"
        idle, sem = self._slots_for(key)
        try:
            if not conn.is_closed():
                idle.put_nowait(conn)
        finally:
            sem.release()

    def warmup(self, target: str, scale: str, count: int | None = None) -> int:
        """Pre-open up to `count` (default: POOL_SIZE) connections and park them.

        Acquires a semaphore slot for each connection so the pool invariant
        (at most ``size`` live connections) is preserved.
        """
        key = f"{target}:{scale}"
        idle, sem = self._slots_for(key)
        want = self._size if count is None else min(count, self._size)
        opened = 0
        for _ in range(want):
            if not sem.acquire(timeout=0):
                break
            try:
                conn = self._new_connection(target, scale)
            except Exception as exc:
                sem.release()
                log.warning("Pool warmup failed for %s after %d/%d: %s", key, opened, want, exc)
                break
            idle.put_nowait(conn)
            opened += 1
        return opened

    def close_all(self) -> None:
        """Close all idle connections in the pool."""
        with self._init_lock:
            for key, idle in self._idle.items():
                while True:
                    try:
                        conn = idle.get_nowait()
                    except Empty:
                        break
                    try:
                        conn.close()
                    except Exception:
                        pass


pool = ConnectionPool()
pool_ready = asyncio.Event()


def execute_query(
    sql: str, target: str, scale: str, binds: list[Any] | None = None
) -> tuple[list[dict[str, Any]], int]:
    conn = pool.acquire(target, scale)
    try:
        with conn.cursor(DictCursor) as cur:
            t0 = time.perf_counter()
            cur.execute(sql, binds or ())
            elapsed_ms = round((time.perf_counter() - t0) * 1000)
            return cur.fetchall(), elapsed_ms
    finally:
        pool.release(target, scale, conn)


def run_dashboard_query(
    target: str,
    scale: str,
    segment: str | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    **options: Any,
) -> tuple[list[dict[str, Any]], int]:
    sql, binds = build_dashboard_query(segment=segment, lookback_days=lookback_days, **options)
    return execute_query(sql, target, scale, binds)


def snowflake_log_context(warehouse: str | None, scale: str | None) -> str:
    try:
        resolved_target = resolve_target(warehouse)
        resolved_scale = resolve_scale(scale)
        wh = warehouse_for_target(resolved_target, resolved_scale)
        schema = schema_for_scale(resolved_scale)
        return f" warehouse={wh} schema={schema}"
    except ValueError:
        return ""


def request_params(
    warehouse: str | None,
    scale: str | None,
    segment: str | None,
    lookback: str | None = None,
) -> tuple[str, str, str | None, int]:
    try:
        target = resolve_target(warehouse)
        resolved_scale = resolve_scale(scale)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if segment is None:
        resolved_segment = None
    else:
        trimmed = segment.strip()
        resolved_segment = trimmed if trimmed else None

    resolved_lookback = resolve_lookback(lookback)

    return target, resolved_scale, resolved_segment, resolved_lookback


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scale = resolve_scale(DEFAULT_SCALE)
    log.info("TPC-H dashboard running at http://localhost:%s", PORT)
    log.info("Pool size: %d per target, workers: %d", POOL_SIZE, WORKERS)
    log.info("Database: %s, default scale: SF%s", DATABASE, scale)
    log.info(
        "Warehouses: interactive=%s, standard=%s",
        warehouse_for_target("interactive", scale),
        warehouse_for_target("standard", scale),
    )
    if CONNECTION_NAME:
        log.info(
            "Snowflake connection: %s (from ~/.snowflake/connections.toml)",
            CONNECTION_NAME,
        )
    else:
        log.info("Snowflake account: %s", base_connection_kwargs().get("account"))
    if credential_cache_enabled():
        log.info("Snowflake credential cache: %s", credential_cache_description())
    try:
        opts = connection_kwargs_for("interactive", scale)
        conn = pool.acquire("interactive", scale)
        pool.release("interactive", scale, conn)
        log.info(
            "Snowflake connection established (%s / %s.%s).",
            opts["warehouse"],
            opts["database"],
            opts["schema"],
        )
    except Exception as exc:
        log.error("Snowflake connection failed: %s", exc)

    if POOL_WARMUP > 0:
        async def _warmup_all() -> None:
            for target in TARGETS:
                try:
                    opened = await asyncio.to_thread(
                        pool.warmup, target, scale, POOL_WARMUP
                    )
                    log.info(
                        "Prewarmed %d/%d connections for %s:%s",
                        opened,
                        POOL_WARMUP,
                        target,
                        scale,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Warmup failed for %s:%s: %s", target, scale, exc)
            pool_ready.set()

        warmup_task = asyncio.create_task(_warmup_all())
    else:
        pool_ready.set()
        warmup_task = None
    yield
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()
    pool.close_all()


app = FastAPI(title="TPC-H Benchmark Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT_DIR / "public"), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.middleware("http")
async def api_logging(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    start = time.perf_counter()
    query = f" {dict(request.query_params)}" if request.query_params else ""
    context = snowflake_log_context(
        request.query_params.get("warehouse"),
        request.query_params.get("scale"),
    )
    route_name = request.url.path

    try:
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        rows = getattr(request.state, "response_rows", "?")
        query_ms = getattr(request.state, "query_elapsed_ms", None)
        if query_ms is not None:
            response.headers["X-Query-Time-Ms"] = str(query_ms)
        log.info(
            "[api] %s %s%s%s %sms rows=%s",
            request.method,
            route_name,
            query,
            context,
            elapsed_ms,
            rows,
        )
        return response
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        log.error(
            "[api] %s %s%s%s %sms error=%s",
            request.method,
            route_name,
            query,
            context,
            elapsed_ms,
            exc,
        )
        raise


def json_api(data: Any, request: Request, query_ms: int | None = None) -> JSONResponse:
    request.state.response_rows = response_row_count(data)
    request.state.query_elapsed_ms = query_ms
    return JSONResponse(serialize_rows(data))


@app.get("/")
def index():
    from fastapi.responses import FileResponse

    return FileResponse(ROOT_DIR / "public" / "index.html")


@app.get("/api/ready")
async def ready() -> dict[str, str]:
    if not pool_ready.is_set():
        raise HTTPException(status_code=503, detail="Pool warming up")
    return {"status": "ready"}


@app.get("/api/config")
def api_config(
    request: Request,
    scale: str | None = Query(default=None),
):
    try:
        resolved_scale = resolve_scale(scale)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return json_api(
        {
            "database": DATABASE,
            "scale": resolved_scale,
            "defaultScale": DEFAULT_SCALE,
            "scales": SCALES,
            "locustAvailable": _locust_available(),
            "lookbackDays": DEFAULT_LOOKBACK_DAYS,
            "lookbackDaysOptions": LOOKBACK_DAYS_OPTIONS,
            "standard": {
                "schema": schema_for_scale(resolved_scale),
                "warehouse": warehouse_for_target("standard", resolved_scale),
            },
            "interactive": {
                "schema": schema_for_scale(resolved_scale),
                "warehouse": warehouse_for_target("interactive", resolved_scale),
            },
        },
        request,
    )


@app.get("/api/segments")
async def api_segments(request: Request):
    target, scale, segment, lookback = request_params(
        warehouse=request.query_params.get("warehouse"),
        scale=request.query_params.get("scale"),
        segment=request.query_params.get("segment"),
        lookback=request.query_params.get("lookback"),
    )
    rows, query_ms = await asyncio.to_thread(
        run_dashboard_query,
        target,
        scale,
        segment,
        lookback,
        select="SELECT DISTINCT l.L_MKTSEGMENT AS market_segment",
        extra_where=["l.L_MKTSEGMENT IS NOT NULL"],
        order_by="ORDER BY market_segment ASC",
    )
    return json_api(
        [r["MARKET_SEGMENT"] for r in rows if r.get("MARKET_SEGMENT")],
        request,
        query_ms,
    )


@app.get("/api/kpis")
async def api_kpis(request: Request):
    target, scale, segment, lookback = request_params(
        warehouse=request.query_params.get("warehouse"),
        scale=request.query_params.get("scale"),
        segment=request.query_params.get("segment"),
        lookback=request.query_params.get("lookback"),
    )
    rows, query_ms = await asyncio.to_thread(
        run_dashboard_query,
        target,
        scale,
        segment,
        lookback,
        select=f"""SELECT
          COUNT(DISTINCT l.L_ORDERKEY) AS total_orders,
          ROUND(SUM({lineitem_revenue("l")}), 2) AS total_revenue,
          COUNT(DISTINCT l.L_CUSTKEY) AS total_customers,
          COUNT(*) AS total_line_items,
          ROUND(SUM({lineitem_revenue("l")}) / NULLIF(COUNT(DISTINCT l.L_ORDERKEY), 0), 2) AS avg_order_value""",
    )
    return json_api(rows[0] if rows else {}, request, query_ms)


@app.get("/api/orders-over-time")
async def api_orders_over_time(request: Request):
    target, scale, segment, lookback = request_params(
        warehouse=request.query_params.get("warehouse"),
        scale=request.query_params.get("scale"),
        segment=request.query_params.get("segment"),
        lookback=request.query_params.get("lookback"),
    )
    rows, query_ms = await asyncio.to_thread(
        run_dashboard_query,
        target,
        scale,
        segment,
        lookback,
        select=f"""SELECT
          DATE_TRUNC('day', l.L_SHIPDATE) AS order_day,
          COUNT(DISTINCT l.L_ORDERKEY) AS total_orders,
          SUM({lineitem_revenue("l")}) AS total_revenue""",
        group_by="GROUP BY 1",
        order_by="ORDER BY order_day ASC",
    )
    return json_api(rows, request, query_ms)


@app.get("/api/by-segment")
async def api_by_segment(request: Request):
    target, scale, segment, lookback = request_params(
        warehouse=request.query_params.get("warehouse"),
        scale=request.query_params.get("scale"),
        segment=request.query_params.get("segment"),
        lookback=request.query_params.get("lookback"),
    )
    rows, query_ms = await asyncio.to_thread(
        run_dashboard_query,
        target,
        scale,
        segment,
        lookback,
        select=f"""SELECT l.L_MKTSEGMENT AS market_segment,
               COUNT(DISTINCT l.L_ORDERKEY) AS order_count,
               SUM({lineitem_revenue("l")}) AS revenue""",
        group_by="GROUP BY 1",
        order_by="" if segment else "ORDER BY revenue DESC",
    )
    return json_api(rows, request, query_ms)


@app.get("/api/by-region")
async def api_by_region(request: Request):
    target, scale, segment, lookback = request_params(
        warehouse=request.query_params.get("warehouse"),
        scale=request.query_params.get("scale"),
        segment=request.query_params.get("segment"),
        lookback=request.query_params.get("lookback"),
    )
    rows, query_ms = await asyncio.to_thread(
        run_dashboard_query,
        target,
        scale,
        segment,
        lookback,
        select=f"""SELECT l.L_REGIONNAME AS region,
               COUNT(DISTINCT l.L_ORDERKEY) AS order_count,
               SUM({lineitem_revenue("l")}) AS revenue""",
        group_by="GROUP BY 1",
        order_by="ORDER BY revenue DESC",
    )
    return json_api(rows, request, query_ms)


@app.get("/api/latest-orders")
async def api_latest_orders(request: Request):
    target, scale, segment, lookback = request_params(
        warehouse=request.query_params.get("warehouse"),
        scale=request.query_params.get("scale"),
        segment=request.query_params.get("segment"),
        lookback=request.query_params.get("lookback"),
    )
    rows, query_ms = await asyncio.to_thread(
        run_dashboard_query,
        target,
        scale,
        segment,
        lookback,
        select=f"""SELECT l.L_ORDERKEY AS order_id,
             MAX(l.L_SHIPDATE) AS order_date,
             l.L_ORDERSTATUS AS status,
             l.L_MKTSEGMENT AS market_segment,
             l.L_REGIONNAME AS region,
             ROUND(SUM({lineitem_revenue("l")}), 2) AS total_amount""",
        group_by="GROUP BY l.L_ORDERKEY, l.L_ORDERSTATUS, l.L_MKTSEGMENT, l.L_REGIONNAME",
        order_by="ORDER BY MAX(l.L_SHIPDATE) DESC, l.L_ORDERKEY DESC",
        limit="LIMIT 20",
    )
    return json_api(rows, request, query_ms)


@app.get("/api/table-stats")
async def api_table_stats(request: Request):
    target, scale, segment, lookback = request_params(
        warehouse=request.query_params.get("warehouse"),
        scale=request.query_params.get("scale"),
        segment=request.query_params.get("segment"),
        lookback=request.query_params.get("lookback"),
    )
    rows, query_ms = await asyncio.to_thread(
        run_dashboard_query,
        target,
        scale,
        segment,
        lookback,
        select="""SELECT
        COUNT(*) AS lineitem_rows,
        COUNT(DISTINCT l.L_ORDERKEY) AS order_rows""",
    )
    return json_api(rows[0] if rows else {}, request, query_ms)


# ---------------------------------------------------------------------------
# Locust proxy — forward start/stop/stats to the Locust REST API
# ---------------------------------------------------------------------------

def _locust_available() -> bool:
    return bool(LOCUST_URL)


@app.get("/api/locust/status")
async def locust_status():
    if not _locust_available():
        return JSONResponse({"available": False})
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LOCUST_URL}/stats/requests")
            data = r.json()
            return JSONResponse({
                "available": True,
                "state": data.get("state", "unknown"),
                "userCount": data.get("user_count", 0),
                "totalRps": round(sum(
                    s.get("current_rps", 0)
                    for s in data.get("stats", [])
                    if s.get("name") != "Aggregated"
                ), 2),
                "workers": data.get("workers", []),
            })
    except Exception:
        return JSONResponse({"available": True, "state": "unreachable", "userCount": 0, "totalRps": 0})


@app.post("/api/locust/start")
async def locust_start(request: Request):
    if not _locust_available():
        raise HTTPException(status_code=404, detail="Locust integration not configured")
    body = await request.json()
    user_count = int(body.get("userCount", 10))
    spawn_rate = int(body.get("spawnRate", 5))
    warehouse = body.get("warehouse", "interactive")
    scale = body.get("scale", str(DEFAULT_SCALE))
    lookback = body.get("lookback", "90")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{LOCUST_URL}/swarm",
                data={
                    "user_count": user_count,
                    "spawn_rate": spawn_rate,
                    "warehouse": warehouse,
                    "scale": scale,
                    "lookback": lookback,
                },
            )
            return JSONResponse(r.json())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Locust unreachable: {exc}") from exc


@app.post("/api/locust/stop")
async def locust_stop():
    if not _locust_available():
        raise HTTPException(status_code=404, detail="Locust integration not configured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{LOCUST_URL}/stop")
            return JSONResponse(r.json())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Locust unreachable: {exc}") from exc


def main() -> None:
    global DEFAULT_SCALE, PORT, WORKERS

    parser = argparse.ArgumentParser(description="TPC-H benchmark dashboard server")
    parser.add_argument("--scale", choices=SCALES, help="Default TPC-H scale factor")
    parser.add_argument("--port", type=int, default=PORT, help="HTTP port")
    parser.add_argument(
        "--workers",
        type=int,
        default=WORKERS,
        help="Number of Uvicorn worker processes (default: WORKERS env or 1)",
    )
    args, _unknown = parser.parse_known_args()

    if args.scale:
        DEFAULT_SCALE = args.scale
    PORT = args.port
    WORKERS = max(1, args.workers)

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        workers=WORKERS if WORKERS > 1 else None,
    )


if __name__ == "__main__":
    main()
