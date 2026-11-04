#!/usr/bin/env bash
# Deploy script supporting two actions:
#   sql      — run the create_lineitem_dashboard SQL (substituting SOLUTION_NAME and SCALE)
#   services — full SPCS deploy (prerequisites, build, push, create services)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

ACTION="${1:-}"

if [[ -z "$ACTION" ]]; then
  echo "Usage: deploy.sh <sql|services>" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Action: sql
# ---------------------------------------------------------------------------
deploy_sql() {
  local scale="${DEFAULT_SCALE:-10}"
  local sql_file="$REPO_DIR/sql/create_lineitem_dashboard.sql"

  if [[ ! -f "$sql_file" ]]; then
    echo "Error: SQL file not found: $sql_file" >&2
    exit 1
  fi

  echo "==> Running create_lineitem_dashboard.sql (SOLUTION_NAME=$SOLUTION_NAME, SCALE=$scale)"

  local rendered
  rendered="$(sed -e "s/{{SOLUTION_NAME}}/${SOLUTION_NAME}/g" \
                  -e "s/{{SCALE}}/${scale}/g" \
                  "$sql_file")"

  snow_sql_run "create_lineitem_dashboard" <<< "$rendered"

  deploy_demo_warehouses "$scale"

  echo "==> Done."
}

# Force the interactive and standard demo warehouses to a fair-comparison
# configuration: X-Small, single cluster. Uses scripting blocks so a SUSPEND
# on an already-suspended warehouse is a no-op. AUTO_RESUME picks them back up
# when the dashboard queries hit them.
deploy_demo_warehouses() {
  local scale="$1"
  local int_wh="${SOLUTION_NAME}_BENCH_WH_INT_${scale}"
  local std_wh="${SOLUTION_NAME}_BENCH_WH_STD_${scale}"

  echo "==> Sizing demo warehouses to XSMALL, single cluster ($int_wh, $std_wh)"

  snow_sql_run "resize demo warehouses" <<EOF
USE ROLE $ROLE;

EXECUTE IMMEDIATE \$\$
BEGIN
  ALTER WAREHOUSE $int_wh SUSPEND;
EXCEPTION
  WHEN OTHER THEN NULL;
END;
\$\$;

ALTER WAREHOUSE $int_wh SET
  WAREHOUSE_SIZE = 'XSMALL',
  MIN_CLUSTER_COUNT = 1,
  MAX_CLUSTER_COUNT = 1;

ALTER WAREHOUSE $int_wh UNSET MAX_CONCURRENCY_LEVEL;

EXECUTE IMMEDIATE \$\$
BEGIN
  ALTER WAREHOUSE $std_wh SUSPEND;
EXCEPTION
  WHEN OTHER THEN NULL;
END;
\$\$;

ALTER WAREHOUSE $std_wh SET
  WAREHOUSE_SIZE = 'XSMALL',
  MIN_CLUSTER_COUNT = 1,
  MAX_CLUSTER_COUNT = 1;

ALTER WAREHOUSE $std_wh UNSET MAX_CONCURRENCY_LEVEL;
EOF
}

# ---------------------------------------------------------------------------
# Action: services
# ---------------------------------------------------------------------------
deploy_services() {
  echo "==> [1/7] Setting up database and schema"
  snow_sql_run "prerequisites setup" <<EOF
USE ROLE $ROLE;
USE WAREHOUSE $DEPLOY_WAREHOUSE;

CREATE DATABASE IF NOT EXISTS $DB;
CREATE SCHEMA IF NOT EXISTS $DB.$SCHEMA;
EOF

  echo "==> [2/7] Creating compute pools and image repository"
  spcs_compute_pool_create "$DASHBOARD_COMPUTE_POOL" "$DASHBOARD_INSTANCE_FAMILY" \
    "$DASHBOARD_MIN_NODES" "$DASHBOARD_MAX_NODES"
  spcs_compute_pool_create "$LOCUST_COMPUTE_POOL" "$LOCUST_INSTANCE_FAMILY" \
    "$LOCUST_MIN_NODES" "$LOCUST_MAX_NODES"
  spcs_image_repo_create "$IMAGE_REPO"

  echo "==> [3/7] Building and pushing container images"
  "$SCRIPT_DIR/build-and-push.sh"

  echo "==> [4/7] Deploying dashboard API service ($DASHBOARD_SERVICE) on pool $DASHBOARD_COMPUTE_POOL"
  spcs_service_upsert "$DASHBOARD_SERVICE" "$DASHBOARD_COMPUTE_POOL" "$SCRIPT_DIR/specs/dashboard.yaml" \
    "$DASHBOARD_MIN_INSTANCES" "$DASHBOARD_MAX_INSTANCES"

  echo "==> [5/7] Deploying isolated API server for locust ($LOCUST_API_SERVICE) on pool $LOCUST_COMPUTE_POOL"
  spcs_service_upsert "$LOCUST_API_SERVICE" "$LOCUST_COMPUTE_POOL" "$SCRIPT_DIR/specs/dashboard.yaml" 1 1

  echo "==> [6/7] Deploying locust service ($LOCUST_SERVICE) on pool $LOCUST_COMPUTE_POOL"
  spcs_service_upsert "$LOCUST_SERVICE" "$LOCUST_COMPUTE_POOL" "$SCRIPT_DIR/specs/locust.yaml" 1 1

  echo "==> [7/7] Waiting for services to become READY (this can take a few minutes)"
  "$SCRIPT_DIR/status.sh" --wait

  echo
  echo "==> Ingress URLs"
  "$SCRIPT_DIR/status.sh" --urls-only
  echo
  echo "Open the dashboard URL in your browser (Snowflake login will prompt on first visit)."
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
case "$ACTION" in
  sql)
    deploy_sql
    ;;
  services)
    deploy_services
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    echo "Usage: deploy.sh <sql|services>" >&2
    exit 1
    ;;
esac
