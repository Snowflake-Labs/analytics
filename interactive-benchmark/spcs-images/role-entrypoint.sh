#!/usr/bin/env bash

set -euo pipefail

API_ENTRYPOINT="${BENCHMARK_API_ENTRYPOINT:-/app/api/entrypoint.sh}"
LOCUST_ENTRYPOINT="${BENCHMARK_LOCUST_ENTRYPOINT:-/app/locust/entrypoint.sh}"
CONTROLLER_PYTHON="${BENCHMARK_CONTROLLER_PYTHON:-/opt/venvs/controller/bin/python}"

case "${BENCHMARK_ROLE:-}" in
  api)
    exec "$API_ENTRYPOINT" "$@"
    ;;
  locust)
    exec "$LOCUST_ENTRYPOINT" "$@"
    ;;
  controller)
    # /app is not writable at runtime, so the default results directory must not be under it.
    export IWB_RESULTS_DIR="${IWB_RESULTS_DIR:-/var/tmp/iwb/results}"
    cd /app/controller
    exec "$CONTROLLER_PYTHON" -m iwb "$@"
    ;;
  *)
    echo "[entrypoint] ERROR: BENCHMARK_ROLE must be 'api', 'locust' or 'controller'." >&2
    exit 64
    ;;
esac
