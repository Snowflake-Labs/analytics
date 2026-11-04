#!/usr/bin/env bash
# SPCS entrypoint for the locust container.
#
# Two-phase execution:
#
#   Phase 1 — Baseline:
#     Runs BaselineUser against /api/run/baseline (no-op) to verify the
#     API/SPCS infrastructure can handle the target concurrency without
#     being a bottleneck. If failure rate or p99 exceed thresholds, the
#     container aborts before touching Snowflake.
#
#   Phase 2 — Snowflake Benchmark:
#     Runs BenchmarkUser against /api/run/interactive (real queries).
#     Only executes if Phase 1 passes. Reports VERDICT: FAIL if the failure
#     rate exceeds BENCHMARK_MAX_FAILURE_PCT.
#
# Both phases reset stats once all users are spawned and extend --run-time by
# the ramp-up time (rounded up to whole seconds), so the reported stats cover
# BASELINE_RUN_TIME / LOCUST_RUN_TIME of full load, plus at most 1 s.
#
# Both phases use --autostart / --autoquit 0 so no external HTTP call is
# needed (SPCS public ingress requires Snowflake auth) and the stats history
# ends when load stops.
#
# After both phases complete, results are printed to stdout and the
# container loops with periodic heartbeats so `snow spcs service logs`
# can retrieve results at any later time. With BENCHMARK_EXIT_AFTER_RUN=1
# it exits instead: 0 = benchmark PASS, 1 = API not ready, 2 = baseline
# FAIL, 3 = benchmark FAIL.

set -uo pipefail

RUN_EPOCH="$(date -u +%s)-$$"
echo "=== RUN_EPOCH ${RUN_EPOCH} ==="

: "${LOCUST_HOST:?LOCUST_HOST must be set (e.g. http://benchmark-api:3000)}"

USERS="${LOCUST_USERS:-10}"
SPAWN="${LOCUST_SPAWN:-5}"
WEB_PORT="${LOCUST_WEB_PORT:-8089}"
RUN_TIME="${LOCUST_RUN_TIME:-3m}"
API_READY_TIMEOUT_SECONDS="${API_READY_TIMEOUT_SECONDS:-300}"
API_READY_POLL_SECONDS="${API_READY_POLL_SECONDS:-2}"

LOCUST_BIN="${BENCHMARK_LOCUST_BIN:-/opt/venvs/locust/bin/locust}"
LOCUST_FILE="${BENCHMARK_LOCUST_FILE:-/app/locust/locustfile.py}"
HTTP_PYTHON="${BENCHMARK_HTTP_PYTHON:-/opt/venvs/locust/bin/python}"
if [[ ! -x "$LOCUST_BIN" && -x /opt/venv/bin/locust ]]; then
  LOCUST_BIN=/opt/venv/bin/locust
  HTTP_PYTHON="${BENCHMARK_HTTP_PYTHON:-/opt/venv/bin/python}"
fi
if [[ ! -f "$LOCUST_FILE" && -f /app/locustfile.py ]]; then
  LOCUST_FILE=/app/locustfile.py
fi
RESULTS_DIR="${BENCHMARK_RESULTS_DIR:-/tmp}"
EXIT_AFTER_RUN="${BENCHMARK_EXIT_AFTER_RUN:-0}"

# Baseline thresholds
BASELINE_RUN_TIME="${BASELINE_RUN_TIME:-1m}"
BASELINE_MAX_FAILURE_PCT="${BASELINE_MAX_FAILURE_PCT:-1}"
BASELINE_MAX_P99_MS="${BASELINE_MAX_P99_MS:-500}"
BENCHMARK_MAX_FAILURE_PCT="${BENCHMARK_MAX_FAILURE_PCT:-1}"

for threshold in BASELINE_MAX_FAILURE_PCT BENCHMARK_MAX_FAILURE_PCT; do
  if [[ ! "${!threshold}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "[entrypoint] ERROR: $threshold must be a number, got '${!threshold}'." >&2
    exit 1
  fi
done
if [[ ! "$BASELINE_MAX_P99_MS" =~ ^[0-9]+$ ]]; then
  echo "[entrypoint] ERROR: BASELINE_MAX_P99_MS must be an integer, got '$BASELINE_MAX_P99_MS'." >&2
  exit 1
fi

# Seconds of full load plus the ramp-up that --reset-stats discards, using
# Locust's dispatch schedule: batches of max(1, floor(spawn)) users every
# batch / spawn seconds, with no wait after the last batch.
total_run_seconds() {
  "$HTTP_PYTHON" - "$1" "$USERS" "$SPAWN" <<'PY'
import math
import sys

from locust.util.timespan import parse_timespan

run_time, users, spawn = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
if spawn <= 0:
    raise SystemExit("LOCUST_SPAWN must be positive")
batch = max(1, math.floor(spawn))
ramp = (math.ceil(users / batch) - 1) * batch / spawn
print(parse_timespan(run_time) + math.ceil(ramp))
PY
}

if ! BASELINE_TOTAL_SECONDS="$(total_run_seconds "$BASELINE_RUN_TIME")" \
  || ! BENCHMARK_TOTAL_SECONDS="$(total_run_seconds "$RUN_TIME")"; then
  echo "[entrypoint] ERROR: invalid run time or spawn rate." >&2
  exit 1
fi

echo "[entrypoint] target=$LOCUST_HOST users=$USERS spawn=$SPAWN"
echo "[entrypoint] baseline: run_time=$BASELINE_RUN_TIME (+ramp, ${BASELINE_TOTAL_SECONDS}s total) max_failure_pct=$BASELINE_MAX_FAILURE_PCT max_p99_ms=$BASELINE_MAX_P99_MS"
echo "[entrypoint] benchmark: run_time=$RUN_TIME (+ramp, ${BENCHMARK_TOTAL_SECONDS}s total) max_failure_pct=$BENCHMARK_MAX_FAILURE_PCT"

wait_for_api() {
  local ready_url="${LOCUST_HOST%/}/api/ready"
  local deadline=$((SECONDS + API_READY_TIMEOUT_SECONDS))

  echo "[entrypoint] Waiting up to ${API_READY_TIMEOUT_SECONDS}s for ${ready_url}"
  while (( SECONDS < deadline )); do
    if "$HTTP_PYTHON" - "$ready_url" >/dev/null 2>&1 <<'PY'
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
    then
      echo "[entrypoint] API is ready."
      return 0
    fi
    sleep "$API_READY_POLL_SECONDS"
  done

  echo "[entrypoint] ERROR: API did not become ready within ${API_READY_TIMEOUT_SECONDS}s." >&2
  return 1
}

# ---------------------------------------------------------------------------
# Helper: print a results banner from a CSV prefix
# ---------------------------------------------------------------------------
print_results() {
  local label="$1" prefix="$2"
  echo ""
  echo "======================== ${label} RESULTS ========================"
  echo "-- ${prefix}_stats.csv --"
  [[ -f "${prefix}_stats.csv" ]] && cat "${prefix}_stats.csv" || echo "(no stats file)"
  echo ""
  echo "-- ${prefix}_failures.csv --"
  [[ -f "${prefix}_failures.csv" ]] && cat "${prefix}_failures.csv" || echo "(no failures file)"
  echo ""
  echo "-- ${prefix}_stats_history.csv (last 5 rows) --"
  [[ -f "${prefix}_stats_history.csv" ]] && tail -5 "${prefix}_stats_history.csv" || echo "(no history file)"
  echo "===================================================================="
}

# ---------------------------------------------------------------------------
# Helper: parse one endpoint's row of a Locust stats CSV. The Aggregated row
# would also count BenchmarkUser's GET /api/queries calls.
# Prints "<fail_pct> <p99> <requests> <failures>", or fails.
# ---------------------------------------------------------------------------
parse_stats() {
  local label="$1" csv="$2" endpoint="$3"
  if [[ ! -f "$csv" ]]; then
    echo "[$label] ERROR: stats file not found: $csv" >&2
    return 1
  fi

  # Locust CSV columns (1-indexed): 3:Request Count 4:Failure Count ... 19:99%
  local result
  result=$(awk -F',' -v endpoint="$endpoint" '
    $2 == endpoint {
      requests = $3 + 0
      failures = $4 + 0
      p99      = $19 + 0
      fail_pct = requests > 0 ? (failures / requests) * 100 : 0
      printf "%.2f %d %d %d", fail_pct, p99, requests, failures
    }
  ' "$csv")

  if [[ -z "$result" ]]; then
    echo "[$label] ERROR: no requests to $endpoint recorded in $csv" >&2
    return 1
  fi
  echo "$result"
}

# A CPU-bound Locust inflates client-side latency; server-side numbers are
# unaffected.
warn_if_cpu_bound() {
  local label="$1" log="$2"
  if grep -q "CPU usage above" "$log"; then
    echo "[$label] WARNING: Locust was CPU-bound during this run; client-side" \
      "percentiles are inflated. Reduce LOCUST_USERS or raise the Locust CPU limit."
  fi
}

# Returns 0 if pass, 1 if fail.
check_baseline() {
  local result fail_pct p99 requests failures
  if ! result="$(parse_stats baseline "$1" /api/run/baseline)"; then
    echo "[baseline] VERDICT: FAIL — no baseline requests were recorded."
    return 1
  fi
  read -r fail_pct p99 requests failures <<< "$result"

  echo "[baseline] requests=$requests failures=$failures failure_pct=${fail_pct}% p99=${p99}ms"
  echo "[baseline] thresholds: max_failure_pct=${BASELINE_MAX_FAILURE_PCT}% max_p99=${BASELINE_MAX_P99_MS}ms"

  local failed=0
  if (( requests == 0 )); then
    echo "[baseline] FAIL: no requests completed"
    failed=1
  fi
  if awk "BEGIN { exit !(${fail_pct} > ${BASELINE_MAX_FAILURE_PCT}) }"; then
    echo "[baseline] FAIL: failure rate ${fail_pct}% exceeds threshold ${BASELINE_MAX_FAILURE_PCT}%"
    failed=1
  fi
  if (( p99 > BASELINE_MAX_P99_MS )); then
    echo "[baseline] FAIL: p99 ${p99}ms exceeds threshold ${BASELINE_MAX_P99_MS}ms"
    failed=1
  fi

  if (( failed )); then
    echo "[baseline] VERDICT: FAIL — the API/SPCS infrastructure cannot handle $USERS concurrent users."
    echo "[baseline] The benchmark will NOT proceed. Consider:"
    echo "[baseline]   - Increasing API_MIN_INSTANCES / API_MAX_INSTANCES"
    echo "[baseline]   - Increasing API compute pool node count"
    echo "[baseline]   - Reducing LOCUST_USERS"
    return 1
  fi

  echo "[baseline] VERDICT: PASS — infrastructure can handle $USERS concurrent users."
  return 0
}

# Latency is judged against the user's goal by the caller; this only gates on
# requests that failed outright. Returns 0 if pass, 1 if fail.
check_benchmark() {
  local result fail_pct p99 requests failures
  if ! result="$(parse_stats benchmark "$1" /api/run/interactive)"; then
    echo "[benchmark] VERDICT: FAIL — no /api/run/interactive requests were recorded."
    return 1
  fi
  read -r fail_pct p99 requests failures <<< "$result"

  echo "[benchmark] requests=$requests failures=$failures failure_pct=${fail_pct}% p99=${p99}ms"
  echo "[benchmark] threshold: max_failure_pct=${BENCHMARK_MAX_FAILURE_PCT}%"

  if (( requests == 0 )); then
    echo "[benchmark] VERDICT: FAIL — no requests completed."
    return 1
  fi
  if awk "BEGIN { exit !(${fail_pct} > ${BENCHMARK_MAX_FAILURE_PCT}) }"; then
    echo "[benchmark] VERDICT: FAIL — failure rate ${fail_pct}% exceeds ${BENCHMARK_MAX_FAILURE_PCT}%;" \
      "the percentiles above are not a valid measurement (see the failures CSV)."
    return 1
  fi

  echo "[benchmark] VERDICT: PASS"
  return 0
}

# ===========================================================================
# Phase 1: Baseline
# ===========================================================================
echo ""
echo "===== PHASE 1: BASELINE TEST ====="
echo "[baseline] Running BaselineUser for $BASELINE_RUN_TIME with $USERS users..."

if ! wait_for_api; then
  exit 1
fi

"$LOCUST_BIN" -f "$LOCUST_FILE" BaselineUser \
  --host "$LOCUST_HOST" \
  --web-host 0.0.0.0 \
  --web-port "$WEB_PORT" \
  --autostart \
  --autoquit 0 \
  --reset-stats \
  --run-time "${BASELINE_TOTAL_SECONDS}s" \
  --csv "${RESULTS_DIR}/baseline_stats" \
  --html "${RESULTS_DIR}/baseline_report.html" \
  -u "$USERS" \
  -r "$SPAWN" 2>&1 | tee "${RESULTS_DIR}/baseline_run.log"

print_results "BASELINE" "${RESULTS_DIR}/baseline_stats"
warn_if_cpu_bound baseline "${RESULTS_DIR}/baseline_run.log"

if ! check_baseline "${RESULTS_DIR}/baseline_stats_stats.csv"; then
  echo ""
  echo "[entrypoint] Baseline failed. Skipping Snowflake benchmark."
  echo "[entrypoint] Review the baseline results above to diagnose the issue."
  if [[ "$EXIT_AFTER_RUN" == 1 ]]; then
    exit 2
  fi

  # Keep container alive for log retrieval
  while true; do
    echo "=== HEARTBEAT $(date -u +%FT%TZ) ==="
    echo "[status] baseline=FAILED benchmark=SKIPPED"
    print_results "BASELINE" "${RESULTS_DIR}/baseline_stats"
    sleep 120
  done
fi

# ===========================================================================
# Phase 2: Snowflake Benchmark
# ===========================================================================
echo ""
echo "===== PHASE 2: SNOWFLAKE BENCHMARK ====="
echo "[benchmark] Running BenchmarkUser for $RUN_TIME with $USERS users..."

"$LOCUST_BIN" -f "$LOCUST_FILE" BenchmarkUser \
  --host "$LOCUST_HOST" \
  --web-host 0.0.0.0 \
  --web-port "$WEB_PORT" \
  --autostart \
  --autoquit 0 \
  --reset-stats \
  --run-time "${BENCHMARK_TOTAL_SECONDS}s" \
  --csv "${RESULTS_DIR}/locust_stats" \
  --html "${RESULTS_DIR}/locust_report.html" \
  -u "$USERS" \
  -r "$SPAWN" 2>&1 | tee "${RESULTS_DIR}/locust_run.log"

print_results "BENCHMARK" "${RESULTS_DIR}/locust_stats"
warn_if_cpu_bound benchmark "${RESULTS_DIR}/locust_run.log"

BENCHMARK_STATUS=COMPLETED
if ! check_benchmark "${RESULTS_DIR}/locust_stats_stats.csv"; then
  BENCHMARK_STATUS=FAILED
fi
if [[ "$EXIT_AFTER_RUN" == 1 ]]; then
  [[ "$BENCHMARK_STATUS" == COMPLETED ]] && exit 0 || exit 3
fi

# Keep container alive so logs remain retrievable
while true; do
  echo "=== HEARTBEAT $(date -u +%FT%TZ) ==="
  echo "[status] baseline=PASSED benchmark=${BENCHMARK_STATUS}"
  echo "-- baseline_stats_stats.csv --"
  [[ -f "${RESULTS_DIR}/baseline_stats_stats.csv" ]] && cat "${RESULTS_DIR}/baseline_stats_stats.csv" || true
  echo "-- locust_stats_stats.csv --"
  [[ -f "${RESULTS_DIR}/locust_stats_stats.csv" ]] && cat "${RESULTS_DIR}/locust_stats_stats.csv" || true
  sleep 120
done
