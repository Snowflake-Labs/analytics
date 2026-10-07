#!/usr/bin/env bash
# Runs locust/entrypoint.sh against a stub API with the locust venv
# (cd locust && uv sync --frozen). Usage: test-locust-entrypoint.sh [locust-venv]

set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${1:-$IMAGE_DIR/locust/.venv}"
TMP_DIR="$(mktemp -d)"
STUB_PID=""
RUN_PID=""

stop_group() {
  [[ -n "$1" ]] || return 0
  kill -- "-$1" 2>/dev/null || true
  wait "$1" 2>/dev/null || true
}
cleanup() {
  stop_group "$RUN_PID"
  stop_group "$STUB_PID"
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -x "$VENV/bin/locust" ]] || fail "locust venv not found at $VENV"

# STUB_MODE: ok | fail_interactive (500s) | no_queries (empty query list).
cat >"$TMP_DIR/stub.py" <<'PY'
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ["STUB_MODE"]
HITS = open(os.environ["STUB_HITS_FILE"], "a", buffering=1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/ready":
            self.reply(200, {"status": "ready"})
        elif self.path == "/api/workload":
            self.reply(200, [] if MODE == "no_queries" else [
                {"id": "heavy", "weight": 90},
                {"id": "light", "weight": 10},
            ])
        else:
            self.reply(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/run/interactive":
            HITS.write(body.get("query_id", "?") + "\n")
        if self.path == "/api/run/interactive" and MODE == "fail_interactive":
            self.reply(500, {"error": "stub failure"})
        else:
            self.reply(200, {"elapsed_ms": 1, "row_count": 1})


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
PY

# Runs the entrypoint until its first heartbeat (it loops on heartbeats after
# both phases). Extra env assignments are passed through.
run_entrypoint() {
  local mode="$1" port web_port
  shift
  : >"$TMP_DIR/hits"
  STUB_MODE="$mode" STUB_HITS_FILE="$TMP_DIR/hits" setsid "$VENV/bin/python" "$TMP_DIR/stub.py" >"$TMP_DIR/port" &
  STUB_PID=$!
  for _ in $(seq 50); do [[ -s "$TMP_DIR/port" ]] && break; sleep 0.1; done
  port="$(cat "$TMP_DIR/port")"
  web_port="$("$VENV/bin/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  rm -rf "$TMP_DIR/results" && mkdir "$TMP_DIR/results"

  env LOCUST_HOST="http://127.0.0.1:$port" LOCUST_USERS=4 LOCUST_SPAWN=2 \
    LOCUST_WEB_PORT="$web_port" LOCUST_RUN_TIME=3s BASELINE_RUN_TIME=3s \
    BENCHMARK_LOCUST_BIN="$VENV/bin/locust" BENCHMARK_HTTP_PYTHON="$VENV/bin/python" \
    BENCHMARK_LOCUST_FILE="$IMAGE_DIR/locust/locustfile.py" \
    BENCHMARK_RESULTS_DIR="$TMP_DIR/results" "$@" \
    setsid bash "$IMAGE_DIR/locust/entrypoint.sh" >"$TMP_DIR/out" 2>&1 &
  RUN_PID=$!
  for _ in $(seq 120); do
    grep -q "=== HEARTBEAT" "$TMP_DIR/out" && break
    kill -0 "$RUN_PID" 2>/dev/null || break
    sleep 0.5
  done
  stop_group "$RUN_PID"
  stop_group "$STUB_PID"
  RUN_PID="" STUB_PID=""
}

expect_finished() {
  grep -q "=== HEARTBEAT" "$TMP_DIR/out" || { cat "$TMP_DIR/out" >&2; fail "entrypoint did not finish: $1"; }
}

# 1. Healthy API. Spawn 4 users at 2/s = 2 batches 1 s apart, so 1 s of ramp
#    is added to each 3 s phase.
run_entrypoint ok
expect_finished ok
grep -q "baseline: run_time=3s (+ramp, 4s total)" "$TMP_DIR/out" || fail "ramp not added to baseline run time"
[[ "$(grep -c "Run time limit set to 4 seconds" "$TMP_DIR/out")" == 2 ]] || fail "both phases should run 4 s"
[[ "$(grep -c "Resetting stats" "$TMP_DIR/out")" == 2 ]] || fail "stats not reset in both phases"
grep -q "\[baseline\] VERDICT: PASS" "$TMP_DIR/out" || fail "baseline did not pass"
grep -q "\[benchmark\] VERDICT: PASS" "$TMP_DIR/out" || fail "benchmark did not pass"
grep -q "benchmark=COMPLETED" "$TMP_DIR/out" || fail "heartbeat status wrong"
[[ -f "$TMP_DIR/results/locust_stats_stats.csv" ]] || fail "results not written to BENCHMARK_RESULTS_DIR"

# 1b. Queries are chosen by weight (90/10). 10 users give ~40 requests, so a
#     heavy share under 75% is a >3 sigma event if weights are applied.
run_entrypoint ok LOCUST_USERS=10 LOCUST_SPAWN=10
expect_finished weights
heavy="$(grep -c '^heavy$' "$TMP_DIR/hits" || true)"
light="$(grep -c '^light$' "$TMP_DIR/hits" || true)"
total=$((heavy + light))
(( total >= 25 )) || fail "too few interactive requests to check weights: $total"
(( heavy * 100 >= total * 75 )) || fail "weights not applied: heavy=$heavy light=$light"
(( light > 0 )) || fail "light query never chosen"

# 2. Every interactive request fails: verdict FAIL, results still printed.
run_entrypoint fail_interactive
expect_finished fail_interactive
grep -q "\[baseline\] VERDICT: PASS" "$TMP_DIR/out" || fail "baseline did not pass"
grep -q "BENCHMARK RESULTS" "$TMP_DIR/out" || fail "results not printed on failure"
grep -q "\[benchmark\] VERDICT: FAIL" "$TMP_DIR/out" || fail "benchmark failures not flagged"
grep -q "benchmark=FAILED" "$TMP_DIR/out" || fail "heartbeat status not FAILED"

# 3. No queries on the stage: no interactive request is ever sent, which must
#    not pass on the strength of the GET /api/queries calls.
run_entrypoint no_queries
expect_finished no_queries
grep -q "\[benchmark\] VERDICT: FAIL — no /api/run/interactive requests" "$TMP_DIR/out" \
  || fail "run without interactive requests was not flagged"
grep -q "benchmark=FAILED" "$TMP_DIR/out" || fail "heartbeat status not FAILED"

# 4. A malformed threshold fails fast instead of disabling the gate.
run_entrypoint ok BENCHMARK_MAX_FAILURE_PCT=1%
grep -q "BENCHMARK_MAX_FAILURE_PCT must be a number" "$TMP_DIR/out" || fail "malformed threshold accepted"
! grep -q "PHASE 1" "$TMP_DIR/out" || fail "ran with a malformed threshold"

echo "Locust entrypoint contract passed."
