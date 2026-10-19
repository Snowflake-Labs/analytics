#!/usr/bin/env bash

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/queries"

cat >"$TMP_DIR/queries/benchmark-query.sql" <<'EOF'
SELECT COUNT(*) FROM ORDERS;
EOF

cat >"$TMP_DIR/config.env" <<EOF
CONNECTION=test
ROLE=TEST_ROLE
DEPLOY_WAREHOUSE=DEPLOY_WH
SOLUTION_NAME=TEST
DB=TEST_DB
SCHEMA=SPCS
QUERIES_STAGE=BENCHMARK_QUERIES
IMAGE_DB=SNOWFLAKE
IMAGE_SCHEMA=IMAGES
IMAGE_REPO=SNOWFLAKE_IMAGES
BENCHMARK_IMAGE=interactive-analytics/interactive-benchmark
IMAGE_TAG=0.2.0
BENCHMARK_IMAGE_ARCH=amd64
API_INSTANCE_FAMILY=CPU_X64_M
LOCUST_INSTANCE_FAMILY=CPU_X64_M
API_ROLE=TEST_ROLE
API_WAREHOUSE=TEST_FALLBACK_WH
INTERACTIVE_WAREHOUSE=TEST_INT_WH
API_DATABASE=SOURCE_DB
INTERACTIVE_SCHEMA=SOURCE_SCHEMA
BENCHMARK_QUERY_DIR=$TMP_DIR/queries
EOF

cat >"$TMP_DIR/snow" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
args="$*"
[[ "$args" == *"--role TEST_ROLE"* ]]

if [[ -f "${BENCHMARK_CONFIG_ENV}.fail" ]]; then
  case "$args" in
    *TEST_INT_WH*)
      echo "Insufficient privileges to operate on warehouse TEST_INT_WH" >&2
      exit 1
      ;;
  esac
fi

if [[ "$args" == *"SHOW ROLES LIKE 'SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE'"* ]]; then
  if [[ -f "${BENCHMARK_CONFIG_ENV}.role" ]]; then
    echo '[{"name":"SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE"}]'
  else
    echo '[]'
  fi
elif [[ "$args" == *"IS_ROLE_IN_SESSION"* ]]; then
  if [[ -f "${BENCHMARK_CONFIG_ENV}.granted" ]]; then
    echo '[{"GRANTED":true}]'
  else
    echo '[{"GRANTED":false}]'
  fi
elif [[ "$args" == *"SELECT COUNT(*) FROM ORDERS"* ]]; then
  echo '[{"content":"GlobalStats"}]'
else
  echo '[{"ROLE":"TEST_ROLE","WAREHOUSE":"TEST_INT_WH"}]'
fi
EOF
chmod +x "$TMP_DIR/snow"

export PATH="$TMP_DIR:$PATH"
export BENCHMARK_CONFIG_ENV="$TMP_DIR/config.env"
# shellcheck disable=SC1090
source "$SCRIPTS_DIR/_lib.sh"

output="$(preflight_benchmark_access 2>"$TMP_DIR/error")"
grep -q "Access preflight passed for role TEST_ROLE" <<<"$output"
if grep -q "SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE" "$TMP_DIR/error" - <<<"$output"; then
  echo "An account without the benchmark role unexpectedly mentioned it." >&2
  exit 1
fi

touch "${BENCHMARK_CONFIG_ENV}.role"
output="$(preflight_benchmark_access 2>"$TMP_DIR/error")"
grep -q "Access preflight passed for role TEST_ROLE" <<<"$output"
grep -q "GRANT ROLE SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE TO ROLE TEST_ROLE" "$TMP_DIR/error"

touch "${BENCHMARK_CONFIG_ENV}.granted"
output="$(preflight_benchmark_access 2>"$TMP_DIR/error")"
grep -q "Role TEST_ROLE has SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE." <<<"$output"
if grep -q "GRANT ROLE" "$TMP_DIR/error"; then
  echo "A role that has the benchmark role was still told to grant it." >&2
  exit 1
fi

API_ROLE=OTHER_ROLE
if preflight_benchmark_access >/dev/null 2>&1; then
  echo "Mismatched service-owner and API roles unexpectedly passed." >&2
  exit 1
fi
API_ROLE=TEST_ROLE

touch "${BENCHMARK_CONFIG_ENV}.fail"
if preflight_benchmark_access >"$TMP_DIR/output" 2>"$TMP_DIR/error"; then
  echo "Missing interactive warehouse access unexpectedly passed." >&2
  exit 1
fi
grep -q "Access preflight failed for the interactive warehouse" "$TMP_DIR/error"
grep -q "GRANT USAGE ON WAREHOUSE TEST_INT_WH TO ROLE TEST_ROLE" "$TMP_DIR/error"

echo "Access preflight contract passed."
