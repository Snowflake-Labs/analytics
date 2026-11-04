#!/usr/bin/env bash
# Rebuild + push images and upgrade both services in-place (URLs preserved).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

"$SCRIPT_DIR/build-and-push.sh"

echo "==> Updating dashboard API service (min=$DASHBOARD_MIN_INSTANCES, max=$DASHBOARD_MAX_INSTANCES)"
spcs_service_upsert "$DASHBOARD_SERVICE" "$DASHBOARD_COMPUTE_POOL" "$SCRIPT_DIR/specs/dashboard.yaml" \
  "$DASHBOARD_MIN_INSTANCES" "$DASHBOARD_MAX_INSTANCES"

echo "==> Updating isolated locust API service"
spcs_service_upsert "$LOCUST_API_SERVICE" "$LOCUST_COMPUTE_POOL" "$SCRIPT_DIR/specs/dashboard.yaml" 1 1

echo "==> Updating locust service"
spcs_service_upsert "$LOCUST_SERVICE" "$LOCUST_COMPUTE_POOL" "$SCRIPT_DIR/specs/locust.yaml" 1 1

"$SCRIPT_DIR/status.sh" --urls-only

