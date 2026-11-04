#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <image-ref>" >&2
  exit 1
fi

IMAGE_REF="$1"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat >"$TMP_DIR/api" <<'EOF'
#!/usr/bin/env bash
echo api-role
EOF
cat >"$TMP_DIR/locust" <<'EOF'
#!/usr/bin/env bash
echo locust-role
EOF
chmod +x "$TMP_DIR/api" "$TMP_DIR/locust"
chmod 755 "$TMP_DIR"

api_output="$(
  docker run --rm \
    -v "$TMP_DIR:/contract:ro" \
    -e BENCHMARK_ROLE=api \
    -e BENCHMARK_API_ENTRYPOINT=/contract/api \
    "$IMAGE_REF"
)"
[[ "$api_output" == "api-role" ]]

locust_output="$(
  docker run --rm \
    -v "$TMP_DIR:/contract:ro" \
    -e BENCHMARK_ROLE=locust \
    -e BENCHMARK_LOCUST_ENTRYPOINT=/contract/locust \
    "$IMAGE_REF"
)"
[[ "$locust_output" == "locust-role" ]]

docker run --rm --entrypoint /opt/venvs/api/bin/python "$IMAGE_REF" \
  -c "import fastapi, snowflake.connector, uvicorn"
docker run --rm --entrypoint /opt/venvs/controller/bin/python -w /app/controller "$IMAGE_REF" \
  -c "import iwb.cli, jsonschema, snowflake.connector"
docker run --rm --entrypoint /opt/venvs/locust/bin/python "$IMAGE_REF" \
  -c "import importlib.util; assert importlib.util.find_spec('locust') is not None"

if docker run --rm -e BENCHMARK_ROLE=invalid "$IMAGE_REF" >/dev/null 2>&1; then
  echo "Invalid role unexpectedly succeeded." >&2
  exit 1
fi

set +e
readiness_output="$(
  docker run --rm \
    -e BENCHMARK_ROLE=locust \
    -e LOCUST_HOST=http://127.0.0.1:9 \
    -e API_READY_TIMEOUT_SECONDS=1 \
    -e API_READY_POLL_SECONDS=1 \
    "$IMAGE_REF" 2>&1
)"
readiness_rc=$?
set -e
if (( readiness_rc == 0 )) || ! grep -q "API did not become ready" <<<"$readiness_output"; then
  echo "Locust API-readiness gate did not fail as expected." >&2
  exit 1
fi

# The controller role as a sandbox runs it, as the image user or as root, with only the session
# token and account env: it renders a connection, rejects the invalid config (exit 30) before
# connecting, and writes the final event and result.json under the default results directory.
echo '{}' >"$TMP_DIR/config.json"
echo dummy-token >"$TMP_DIR/token"
for user in "" 0; do
  set +e
  controller_output="$(
    docker run --rm ${user:+--user "$user"} \
      -v "$TMP_DIR:/contract:ro" \
      -e BENCHMARK_ROLE=controller \
      -e SNOWFLAKE_ACCOUNT=CONTRACT \
      -e SNOWFLAKE_ACCOUNT_URL=contract.invalid \
      -e SNOWFLAKE_TOKEN_FILE_PATH=/contract/token \
      --entrypoint bash "$IMAGE_REF" -c \
      'benchmark-entrypoint run --config /contract/config.json; rc=$?; cat /var/tmp/iwb/results/*/result.json; exit $rc' 2>&1
  )"
  controller_rc=$?
  set -e
  if (( controller_rc != 30 )) || ! grep -q '"step": "RUN", "status": "failed"' <<<"$controller_output" \
    || ! grep -q '"exit_code": 30' <<<"$controller_output"; then
    echo "Controller role (user ${user:-from image}) did not reject the config with exit 30 and" \
      "a result.json (exit $controller_rc):" >&2
    echo "$controller_output" >&2
    exit 1
  fi
done

echo "Same-image API, Locust and controller contract passed for $IMAGE_REF."
