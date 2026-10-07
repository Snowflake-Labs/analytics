"""
Locust workload for the interactive warehouse benchmark API.

Two user classes:
  - BenchmarkUser: fetches the weighted workload from the API server
    (GET /api/workload) and POSTs query IDs, chosen by weight, to
    POST /api/run/interactive.
  - BaselineUser: POSTs to POST /api/run/baseline with a static payload.
    Measures pure API/infra throughput without touching Snowflake.

Examples:
  # Run benchmark (Snowflake queries):
  uv run locust -f locustfile.py BenchmarkUser --host http://localhost:3000
  # Run baseline (no-op, infra only):
  uv run locust -f locustfile.py BaselineUser --host http://localhost:3000 \
      --headless -u 20 -r 5 -t 1m
"""

from __future__ import annotations

import random

from locust import between, task
from locust.contrib.fasthttp import FastHttpUser


class BenchmarkUser(FastHttpUser):
    """Sends benchmark queries to the interactive warehouse API endpoint."""

    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        resp = self.client.get("/api/workload")
        resp.raise_for_status()
        workload = resp.json()
        if not workload:
            raise RuntimeError(
                "No queries loaded on the API server. "
                "Upload .sql files to the benchmark queries stage."
            )
        self.query_ids = [entry["id"] for entry in workload]
        self.weights = [entry["weight"] for entry in workload]

    @task
    def run_query(self) -> None:
        query_id = random.choices(self.query_ids, weights=self.weights)[0]
        payload = {"query_id": query_id}
        endpoint = "/api/run/interactive"
        with self.client.post(
            endpoint,
            json=payload,
            name=endpoint,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"{endpoint} status {response.status_code}: {response.text}")


class BaselineUser(FastHttpUser):
    """Hits the no-op baseline endpoint to measure pure API/infra throughput."""

    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        """Wait for the API server to be ready before sending measured requests.

        Uses urllib directly (not self.client) so the warmup latency is
        invisible to Locust stats — no 30s cold-start outlier in p99.
        """
        import time
        import urllib.request

        ready_url = f"{self.host}/api/ready"
        for _ in range(60):
            try:
                with urllib.request.urlopen(ready_url, timeout=5):
                    return
            except Exception:
                pass
            time.sleep(1)

    @task
    def run_baseline(self) -> None:
        endpoint = "/api/run/baseline"
        with self.client.post(
            endpoint,
            json={"query_id": "baseline"},
            name=endpoint,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"{endpoint} status {response.status_code}: {response.text}")
