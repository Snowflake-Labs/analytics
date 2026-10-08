#!/usr/bin/env bash
# Inside a Cortex Sandbox (or any Linux host with Python 3.11+): install the pinned API,
# Locust and controller environments from their uv.lock files, then run one benchmark.
#
# Usage: bootstrap.sh <config.json>
# The bundle directory must contain api/, locust/ and controller/ from spcs-images/.
#
# Env: IWB_CONNECTION (default "default", the sandbox-rendered connection),
#      IWB_RESULTS_DIR (optional; results are copied there when the run ends),
#      IWB_WORK_DIR (default /var/tmp/iwb; results are written to $IWB_WORK_DIR/results).

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <config.json>" >&2
  exit 64
fi

CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
UV_VERSION=0.12.17
WORK_DIR="${IWB_WORK_DIR:-/var/tmp/iwb}"

for project in api locust controller; do
  if [[ ! -f "$CODE_DIR/$project/uv.lock" ]]; then
    echo "[bootstrap] ERROR: $CODE_DIR/$project/uv.lock not found; bundle is incomplete." >&2
    exit 65
  fi
done

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"[bootstrap] ERROR: Python 3.11+ required, found {sys.version.split()[0]}")
PY

echo '{"iwb":"bootstrap","status":"installing"}'
python3 -m venv "$WORK_DIR/tools"
"$WORK_DIR/tools/bin/pip" install --quiet --disable-pip-version-check "uv==$UV_VERSION"
for project in api locust controller; do
  (cd "$CODE_DIR/$project" &&
    UV_PROJECT_ENVIRONMENT="$WORK_DIR/venvs/$project" \
      "$WORK_DIR/tools/bin/uv" sync --frozen --no-dev --no-install-project --quiet)
done
echo '{"iwb":"bootstrap","status":"installed"}'

export IWB_API_PYTHON="$WORK_DIR/venvs/api/bin/python"
export IWB_API_SERVER="$CODE_DIR/api/server.py"
export IWB_LOCUST_ENTRYPOINT="$CODE_DIR/locust/entrypoint.sh"
export BENCHMARK_LOCUST_BIN="$WORK_DIR/venvs/locust/bin/locust"
export BENCHMARK_LOCUST_FILE="$CODE_DIR/locust/locustfile.py"
export BENCHMARK_HTTP_PYTHON="$WORK_DIR/venvs/locust/bin/python"

cd "$CODE_DIR/controller"
rc=0
# Backgrounded so its PID can be recorded: the launcher SIGTERMs it to trigger teardown.
"$WORK_DIR/venvs/controller/bin/python" -m iwb run \
  --config "$CONFIG" \
  --results-dir "$WORK_DIR/results" \
  --connection "${IWB_CONNECTION:-default}" < /dev/null &
echo "$!" > "$WORK_DIR/controller.pid"
wait "$!" || rc=$?

# Stage mounts do not support truncate(), which Locust's CSV writer needs, so copy at the end.
if [[ -n "${IWB_RESULTS_DIR:-}" ]] && ! cp -r "$WORK_DIR/results/." "$IWB_RESULTS_DIR/"; then
  echo '{"iwb":"bootstrap","status":"results_copy_failed"}'
  if [[ $rc -eq 0 ]]; then rc=1; fi
fi
exit "$rc"
