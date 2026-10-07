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
docker run --rm -e BENCHMARK_ROLE=controller "$IMAGE_REF" run --help >/dev/null
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

echo "Same-image API and Locust contract passed for $IMAGE_REF."
