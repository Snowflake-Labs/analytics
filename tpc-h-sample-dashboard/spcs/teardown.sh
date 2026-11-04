#!/usr/bin/env bash
# Drop the dashboard + locust services, compute pools, and image repository.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

spcs service drop "$LOCUST_SERVICE" --if-exists
spcs service drop "$LOCUST_API_SERVICE" --if-exists
spcs service drop "$DASHBOARD_SERVICE" --if-exists

spcs compute-pool stop-all "$DASHBOARD_COMPUTE_POOL" 2>/dev/null || true
spcs compute-pool stop-all "$LOCUST_COMPUTE_POOL" 2>/dev/null || true
spcs compute-pool drop "$DASHBOARD_COMPUTE_POOL" --if-exists
spcs compute-pool drop "$LOCUST_COMPUTE_POOL" --if-exists

spcs image-repository drop "$IMAGE_REPO" --if-exists

echo "Dropped services, compute pools ('$DASHBOARD_COMPUTE_POOL', '$LOCUST_COMPUTE_POOL'), and image repo '$IMAGE_REPO'."

