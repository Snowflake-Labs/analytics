#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat >"$TMP_DIR/api" <<'EOF'
#!/usr/bin/env bash
printf 'api:%s\n' "$*"
EOF
cat >"$TMP_DIR/locust" <<'EOF'
#!/usr/bin/env bash
printf 'locust:%s\n' "$*"
EOF
chmod +x "$TMP_DIR/api" "$TMP_DIR/locust"

api_output="$(
  BENCHMARK_ROLE=api \
  BENCHMARK_API_ENTRYPOINT="$TMP_DIR/api" \
  bash "$SCRIPT_DIR/role-entrypoint.sh" one two
)"
[[ "$api_output" == "api:one two" ]]

locust_output="$(
  BENCHMARK_ROLE=locust \
  BENCHMARK_LOCUST_ENTRYPOINT="$TMP_DIR/locust" \
  bash "$SCRIPT_DIR/role-entrypoint.sh" three
)"
[[ "$locust_output" == "locust:three" ]]

if BENCHMARK_ROLE=invalid bash "$SCRIPT_DIR/role-entrypoint.sh" >/dev/null 2>&1; then
  echo "Invalid role unexpectedly succeeded." >&2
  exit 1
fi

echo "Role entrypoint contract passed."
