#!/usr/bin/env bash
# Run one benchmark with the controller for a host that drives it through exec calls, and copy
# its results (sandbox/launch.py; Snowsight instead runs `benchmark-entrypoint run` as the main
# process, see sandbox/README.md).
#
# Usage: run.sh <config.json>
# In the benchmark image the defaults below already point at the installed environments;
# sandbox/bootstrap.sh overrides them after installing from source.
#
# Env: IWB_CONNECTION (default "default"),
#      IWB_RESULTS_COPY_DIR (optional, e.g. a stage mount; results are copied there at the end),
#      IWB_WORK_DIR (default /var/tmp/iwb; results are written to $IWB_WORK_DIR/results).
#
# Writes $IWB_WORK_DIR/controller.pid; SIGTERM that PID to tear the run down. Creating
# $IWB_WORK_DIR/stop before reading the PID covers a stop that arrives before the controller starts.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <config.json>" >&2
  exit 64
fi

CONTROLLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
WORK_DIR="${IWB_WORK_DIR:-/var/tmp/iwb}"
CONTROLLER_PYTHON="${IWB_CONTROLLER_PYTHON:-/opt/venvs/controller/bin/python}"
CONNECTION="${IWB_CONNECTION:-default}"

mkdir -p "$WORK_DIR"

cd "$CONTROLLER_DIR"
# A stop before the controller installs its signal handlers would kill it without a result.
if [[ -e "$WORK_DIR/stop" ]]; then
  echo '{"iwb":"bootstrap","status":"stopped_before_start"}'
  exit 143
fi
rc=0
# Backgrounded so its PID can be recorded: the launcher SIGTERMs it to trigger teardown.
"$CONTROLLER_PYTHON" -m iwb run \
  --config "$CONFIG" \
  --results-dir "$WORK_DIR/results" \
  --connection "$CONNECTION" < /dev/null &
pid=$!
echo "$pid" > "$WORK_DIR/controller.pid"
if [[ -e "$WORK_DIR/stop" ]]; then
  kill -TERM "$pid" 2>/dev/null || true
fi
wait "$pid" || rc=$?

# Stage mounts do not support truncate(), which Locust's CSV writer needs, so copy at the end.
if [[ -n "${IWB_RESULTS_COPY_DIR:-}" ]] && ! cp -r "$WORK_DIR/results/." "$IWB_RESULTS_COPY_DIR/"; then
  echo '{"iwb":"bootstrap","status":"results_copy_failed"}'
  if [[ $rc -eq 0 ]]; then rc=1; fi
fi
exit "$rc"
