"""
Locust workload for the TPC-H benchmark dashboard API.

Mirrors a user opening the dashboard and refreshing data (no browser):
  1. GET /api/config
  2. GET /api/segments
  3. Parallel fetch of dashboard panels (same as the UI's fetchData)

Warehouse and scale can be set three ways (last one wins):
  1. Env vars: WAREHOUSE, SCALE
  2. CLI flags: --warehouse, --scale
  3. Locust web UI: two dropdowns on the "Start new swarm" form

Examples:
  uv run locust -f locustfile.py --host http://localhost:3000
  uv run locust -f locustfile.py --host http://localhost:3000 \
      --warehouse standard --scale 100 --headless -u 20 -r 5 -t 2m
"""

from __future__ import annotations

import os
import random

import gevent
from locust import HttpUser, between, events, task

WAREHOUSES = ["interactive", "standard"]
SCALES = ["1", "10", "100", "1000"]
LOOKBACK_DAYS = ["15", "30", "90", "180"]


@events.init_command_line_parser.add_listener
def _register_cli_args(parser):
    """Expose --warehouse and --scale as CLI flags AND as fields in the
    locust web UI's 'Start new swarm' form (via include_in_web_ui=True)."""
    parser.add_argument(
        "--warehouse",
        type=str,
        choices=WAREHOUSES,
        default=os.environ.get("WAREHOUSE", "interactive"),
        env_var="WAREHOUSE",
        include_in_web_ui=True,
        help="TPC-H warehouse target",
    )
    parser.add_argument(
        "--scale",
        type=str,
        choices=SCALES,
        default=os.environ.get("SCALE", os.environ.get("DEFAULT_SCALE", "100")),
        env_var="SCALE",
        include_in_web_ui=True,
        help="TPC-H scale factor",
    )
    parser.add_argument(
        "--lookback",
        type=str,
        choices=LOOKBACK_DAYS,
        default=os.environ.get("LOOKBACK", "90"),
        env_var="LOOKBACK",
        include_in_web_ui=True,
        help="Lookback window in days",
    )


DASHBOARD_ENDPOINTS = (
    "/api/orders-over-time",
    "/api/kpis",
    "/api/by-segment",
    "/api/by-region",
    "/api/latest-orders",
    "/api/table-stats",
)


class DashboardUser(HttpUser):
    """Simulates one dashboard user hitting the JSON API."""

    wait_time = between(0.8, 2.0)

    def _resolve_options(self) -> tuple[str, str, str]:
        """Read warehouse/scale/lookback from parsed CLI / web-UI options.  Falls back
        to env vars if parsed_options isn't available (very old locust)."""
        opts = getattr(self.environment, "parsed_options", None)
        wh = getattr(opts, "warehouse", None) or os.environ.get("WAREHOUSE", "interactive")
        sc = getattr(opts, "scale", None) or os.environ.get("SCALE", "100")
        lb = getattr(opts, "lookback", None) or os.environ.get("LOOKBACK", "90")
        if wh not in WAREHOUSES:
            wh = "interactive"
        if sc not in SCALES:
            sc = "100"
        if lb not in LOOKBACK_DAYS:
            lb = "90"
        return wh, sc, lb

    def on_start(self) -> None:
        self.warehouse, self.scale, self.lookback = self._resolve_options()
        self.segment = ""
        self.segments: list[str] = []

        with self.client.get("/api/config", name="/api/config", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"config status {response.status_code}")

        with self.client.get(
            "/api/segments",
            params={"warehouse": self.warehouse, "scale": self.scale},
            name="/api/segments",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"segments status {response.status_code}")
                return
            body = response.json()
            if isinstance(body, list):
                self.segments = [s for s in body if s]

    def _params(self, segment: str | None = None) -> dict[str, str]:
        params = {"warehouse": self.warehouse, "scale": self.scale, "lookback": self.lookback}
        seg = self.segment if segment is None else segment
        if seg:
            params["segment"] = seg
        return params

    def _fetch_dashboard(self, segment: str = "") -> None:
        endpoints = list(DASHBOARD_ENDPOINTS)
        if segment:
            endpoints.remove("/api/by-segment")

        params = self._params(segment=segment)

        def fetch(path: str) -> None:
            self.client.get(path, params=params, name=path)

        gevent.joinall([gevent.spawn(fetch, path) for path in endpoints])

    @task(8)
    def refresh_dashboard(self) -> None:
        """Initial load / refresh with current filters (no segment)."""
        self.segment = ""
        self._fetch_dashboard()

    @task(4)
    def refresh_with_segment(self) -> None:
        """Change segment filter and refresh (skips /api/by-segment like the UI)."""
        if not self.segments:
            self._fetch_dashboard()
            return
        self.segment = random.choice(self.segments)
        self._fetch_dashboard(segment=self.segment)
