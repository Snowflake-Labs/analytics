#!/usr/bin/env bash

set -euo pipefail

API_ENTRYPOINT="${BENCHMARK_API_ENTRYPOINT:-/app/api/entrypoint.sh}"
LOCUST_ENTRYPOINT="${BENCHMARK_LOCUST_ENTRYPOINT:-/app/locust/entrypoint.sh}"

case "${BENCHMARK_ROLE:-}" in
  api)
    exec "$API_ENTRYPOINT" "$@"
    ;;
  locust)
    exec "$LOCUST_ENTRYPOINT" "$@"
    ;;
  controller)
    cd /app/controller
    exec /opt/venvs/controller/bin/python -m iwb "$@"
    ;;
  *)
    echo "[entrypoint] ERROR: BENCHMARK_ROLE must be 'api', 'locust' or 'controller'." >&2
    exit 64
    ;;
esac
