#!/usr/bin/env bash
# Shared helpers sourced by all orchestration scripts.
# Loads config.env and defines wrappers around `snow sql` and `snow spcs`.

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPCS_DIR="$(cd "$SCRIPTS_DIR/../spcs" && pwd)"
REPO_DIR="$(cd "$SPCS_DIR/../.." && pwd)"

CONFIG_ENV_FILE="${BENCHMARK_CONFIG_ENV:-$SPCS_DIR/config.env}"
# shellcheck disable=SC1090
source "$CONFIG_ENV_FILE"

: "${CONNECTION:?CONNECTION must be set in config.env}"
: "${DB:?DB must be set}"
: "${SCHEMA:?SCHEMA must be set}"
: "${BENCHMARK_IMAGE:?BENCHMARK_IMAGE must be set}"
: "${IMAGE_TAG:?IMAGE_TAG must be set}"
: "${BENCHMARK_IMAGE_ARCH:?BENCHMARK_IMAGE_ARCH must be set}"
: "${BENCHMARK_QUERY_DIR:=$REPO_DIR/benchmark/test}"

export CONNECTION DB SCHEMA QUERIES_STAGE ROLE DEPLOY_WAREHOUSE \
       SOLUTION_NAME \
       IMAGE_DB IMAGE_SCHEMA IMAGE_REPO \
       API_COMPUTE_POOL API_INSTANCE_FAMILY \
       API_MIN_NODES API_MAX_NODES \
       API_MIN_INSTANCES API_MAX_INSTANCES \
       LOCUST_COMPUTE_POOL LOCUST_INSTANCE_FAMILY \
       LOCUST_MIN_NODES LOCUST_MAX_NODES \
       API_SERVICE LOCUST_SERVICE \
       BENCHMARK_IMAGE IMAGE_TAG BENCHMARK_IMAGE_ARCH \
       API_DATABASE API_ROLE API_WAREHOUSE API_PORT POOL_SIZE \
       API_WORKERS API_POOL_WARMUP API_POOL_ACQUIRE_TIMEOUT \
       API_CPU_REQUEST API_CPU_LIMIT \
       API_MEMORY_REQUEST API_MEMORY_LIMIT \
       INTERACTIVE_WAREHOUSE INTERACTIVE_SCHEMA \
       LOCUST_HOST LOCUST_WEB_PORT LOCUST_USERS LOCUST_SPAWN \
       LOCUST_RUN_TIME API_READY_TIMEOUT_SECONDS API_READY_POLL_SECONDS \
       BENCHMARK_QUERY_DIR

# Defaults for tuning knobs that may be missing on older config.env files.
: "${API_WORKERS:=4}"
: "${API_POOL_WARMUP:=10}"
: "${API_POOL_ACQUIRE_TIMEOUT:=30}"
: "${API_CPU_REQUEST:=2000m}"
: "${API_CPU_LIMIT:=4000m}"
: "${API_MEMORY_REQUEST:=2Gi}"
: "${API_MEMORY_LIMIT:=4Gi}"
: "${API_READY_TIMEOUT_SECONDS:=600}"
: "${API_READY_POLL_SECONDS:=2}"
: "${BASELINE_MAX_P99_MS:=500}"
: "${BASELINE_MAX_FAILURE_PCT:=1}"
: "${BENCHMARK_MAX_FAILURE_PCT:=1}"
export API_WORKERS API_POOL_WARMUP API_POOL_ACQUIRE_TIMEOUT \
       API_CPU_REQUEST API_CPU_LIMIT API_MEMORY_REQUEST API_MEMORY_LIMIT \
       API_READY_TIMEOUT_SECONDS API_READY_POLL_SECONDS \
       BASELINE_MAX_P99_MS BASELINE_MAX_FAILURE_PCT BENCHMARK_MAX_FAILURE_PCT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

require_cmd snow
require_cmd envsubst
require_cmd python3

# Run a SQL statement against $CONNECTION and print JSON output.
snow_sql() {
  snow sql --connection "$CONNECTION" --format json "$@"
}

# Run a SQL statement quietly and return the raw stdout.
# With -i, snow returns a JSON list-of-lists (one per statement).
# We flatten to the LAST non-empty rowset for backward compatibility with
# callers that expect a flat list of dicts.
snow_sql_quiet() {
  snow sql --connection "$CONNECTION" --silent --format json -i "$@" | python3 -c '
import sys, json
d = json.load(sys.stdin)
if isinstance(d, list) and d and isinstance(d[0], list):
    # Pick last non-empty statement rowset, else last one.
    last = d[-1]
    for r in reversed(d):
        if r:
            last = r
            break
    print(json.dumps(last))
else:
    print(json.dumps(d))
'
}

# Run a SQL script (heredoc on stdin) suppressing normal table output.
# Only prints "SQL error:" plus the captured output on failure.
snow_sql_run() {
  local label="${1:-SQL}"
  local out
  local rc=0
  out="$(snow sql --connection "$CONNECTION" -i 2>&1)" || rc=$?
  if (( rc != 0 )); then
    echo "SQL error while running: $label" >&2
    echo "$out" >&2
    return "$rc"
  fi
  return 0
}

validate_image_config() {
  if [[ ! "$BENCHMARK_IMAGE" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "Invalid BENCHMARK_IMAGE: $BENCHMARK_IMAGE" >&2
    return 1
  fi
  if [[ ! "$IMAGE_TAG" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    echo "IMAGE_TAG must be an immutable release version (e.g. 0.2.0): $IMAGE_TAG" >&2
    return 1
  fi
  # The skill reads [benchmark] VERDICT, which images before 0.2.0 never print.
  if (( BASH_REMATCH[1] == 0 && BASH_REMATCH[2] < 2 )); then
    echo "IMAGE_TAG $IMAGE_TAG is too old: this skill requires image 0.2.0 or later." >&2
    return 1
  fi
  case "$BENCHMARK_IMAGE_ARCH" in
    amd64|arm64) ;;
    *)
      echo "BENCHMARK_IMAGE_ARCH must be amd64 or arm64." >&2
      return 1
      ;;
  esac
}

validate_pool_architecture() {
  local family
  for family in "$API_INSTANCE_FAMILY" "$LOCUST_INSTANCE_FAMILY"; do
    case "$BENCHMARK_IMAGE_ARCH:$family" in
      amd64:CPU_X64_*|arm64:GEN_ARM_*) ;;
      *)
        echo "Image architecture $BENCHMARK_IMAGE_ARCH is incompatible with instance family $family." >&2
        return 1
        ;;
    esac
  done
}

validate_unquoted_identifier() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_$]*$ ]]; then
    echo "$name must be an unquoted Snowflake identifier: $value" >&2
    return 1
  fi
}

BENCHMARK_SYSTEM_ROLE=SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE

# Accounts with Interactive Analytics have a Snowflake-owned role holding the account privileges
# deploy.sh needs. Recommend it when it exists but ROLE lacks it; ROLE's own privileges still work.
check_benchmark_system_role() {
  local rows granted
  rows="$(snow sql --connection "$CONNECTION" --role "$ROLE" --silent --format json -q \
    "SHOW ROLES LIKE '$BENCHMARK_SYSTEM_ROLE'")"
  if ! python3 -c 'import json, sys; sys.exit(0 if json.loads(sys.argv[1]) else 1)' "$rows"; then
    return 0
  fi
  granted="$(snow sql --connection "$CONNECTION" --role "$ROLE" --silent --format json -q \
    "SELECT IS_ROLE_IN_SESSION('$BENCHMARK_SYSTEM_ROLE') AS GRANTED")"
  if python3 -c 'import json, sys; sys.exit(0 if json.loads(sys.argv[1])[0]["GRANTED"] else 1)' "$granted"; then
    echo "Role $ROLE has $BENCHMARK_SYSTEM_ROLE."
    return 0
  fi
  cat >&2 <<EOF
Note: this account has $BENCHMARK_SYSTEM_ROLE (CREATE DATABASE, CREATE WAREHOUSE,
CREATE COMPUTE POOL and BIND SERVICE ENDPOINT), but $ROLE does not. Continuing with
$ROLE's own privileges; to rely on the benchmark role instead, ask an administrator to run:

USE ROLE ACCOUNTADMIN;
GRANT ROLE $BENCHMARK_SYSTEM_ROLE TO ROLE $ROLE;
EOF
}

print_access_remediation() {
  cat >&2 <<EOF
Ask an administrator to grant the service-owner role access to the benchmark
warehouses and source data ($BENCHMARK_SYSTEM_ROLE does not include data access),
then rerun deploy.sh:

USE ROLE ACCOUNTADMIN;
GRANT USAGE ON WAREHOUSE $INTERACTIVE_WAREHOUSE TO ROLE $API_ROLE;
GRANT USAGE ON WAREHOUSE $API_WAREHOUSE TO ROLE $API_ROLE;
GRANT USAGE ON DATABASE $API_DATABASE TO ROLE $API_ROLE;
GRANT USAGE ON SCHEMA $API_DATABASE.$INTERACTIVE_SCHEMA TO ROLE $API_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA $API_DATABASE.$INTERACTIVE_SCHEMA TO ROLE $API_ROLE;
EOF
}

probe_warehouse_access() {
  local label="$1"
  local warehouse="$2"
  local output

  if ! output="$(snow sql \
    --connection "$CONNECTION" \
    --role "$API_ROLE" \
    --silent \
    --format json \
    -q "USE WAREHOUSE $warehouse; USE DATABASE $API_DATABASE; USE SCHEMA $API_DATABASE.$INTERACTIVE_SCHEMA; SELECT CURRENT_ROLE() AS ROLE, CURRENT_WAREHOUSE() AS WAREHOUSE" \
    2>&1)"; then
    echo "Access preflight failed for the $label warehouse '$warehouse':" >&2
    echo "$output" >&2
    print_access_remediation
    return 1
  fi
}

probe_query_access() {
  local query_files=("$BENCHMARK_QUERY_DIR"/*.sql)
  local query_file query output

  if [[ ! -e "${query_files[0]}" ]]; then
    echo "No benchmark .sql files found in $BENCHMARK_QUERY_DIR." >&2
    return 1
  fi

  for query_file in "${query_files[@]}"; do
    query="$(<"$query_file")"
    if ! output="$(snow sql \
      --connection "$CONNECTION" \
      --role "$API_ROLE" \
      --silent \
      --format json \
      -q "USE WAREHOUSE $INTERACTIVE_WAREHOUSE; USE DATABASE $API_DATABASE; USE SCHEMA $API_DATABASE.$INTERACTIVE_SCHEMA; $query" \
      2>&1)"; then
      echo "Source-data preflight failed for $query_file:" >&2
      echo "$output" >&2
      print_access_remediation
      return 1
    fi
  done
}

preflight_benchmark_access() {
  local name

  if [[ "$API_ROLE" != "$ROLE" ]]; then
    cat >&2 <<EOF
API_ROLE ($API_ROLE) must equal ROLE ($ROLE).
SPCS OAuth tokens can use the service-owner role or PUBLIC; deploy the service
and run the API with the same role.
EOF
    return 1
  fi

  for name in ROLE API_ROLE API_DATABASE INTERACTIVE_SCHEMA \
    INTERACTIVE_WAREHOUSE API_WAREHOUSE; do
    validate_unquoted_identifier "$name" "${!name}" || return 1
  done

  check_benchmark_system_role || return 1
  probe_warehouse_access "interactive" "$INTERACTIVE_WAREHOUSE" || return 1
  probe_warehouse_access "fallback" "$API_WAREHOUSE" || return 1
  probe_query_access || return 1

  echo "Access preflight passed for role $API_ROLE."
}

preflight_benchmark_image() {
  local rows
  validate_image_config
  validate_pool_architecture

  rows="$(snow sql --connection "$CONNECTION" --role "$ROLE" --silent --format json -q \
    "SHOW IMAGES LIKE '${BENCHMARK_IMAGE}' IN IMAGE REPOSITORY ${IMAGE_DB}.${IMAGE_SCHEMA}.${IMAGE_REPO}")"

  IMAGE_ROWS="$rows" python3 - "$BENCHMARK_IMAGE" "$IMAGE_TAG" <<'PY'
import json
import os
import sys

image_name, expected_tag = sys.argv[1:]
rows = json.loads(os.environ["IMAGE_ROWS"])
for row in rows:
    if row.get("image_name") != image_name:
        continue
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(",")]
    if expected_tag in tags:
        print(
            "Using image "
            f"{row.get('image_path', image_name + ':' + expected_tag)} "
            f"(digest {row.get('digest', 'unknown')})"
        )
        break
else:
    sys.stderr.write(
        f"Image {image_name}:{expected_tag} was not found in the configured repository.\n"
        "Check IMAGE_TAG. A newly released tag does not reach every deployment's System\n"
        "Registry at once; if it is not here yet, wait for the rollout. Do not pin a tag\n"
        "older than 0.2.0.\n"
    )
    raise SystemExit(1)
PY
}

# Render a spec yaml with env vars substituted.
render_spec() {
  local spec="$1"
  envsubst < "$spec"
}
