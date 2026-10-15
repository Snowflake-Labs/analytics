#!/usr/bin/env bash
# controller/run.sh: controller arguments, the PID and stop-file protocol, and the results copy.
# The controller is a stub. Usage: test-controller-run.sh

set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$IMAGE_DIR/controller/run.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# Stub controller: records its args, writes a result, then exits with STUB_RC, or with 143 if
# SIGTERM arrives while it sleeps STUB_SLEEP seconds.
cat >"$TMP_DIR/python" <<'STUB'
#!/usr/bin/env bash
results="${@: -3:1}"
echo "args=$*" >"$STUB_OUT"
mkdir -p "$results/RUN_1" && echo '{}' >"$results/RUN_1/result.json"
trap 'exit 143' TERM
sleep "${STUB_SLEEP:-0}" & wait $!
exit "${STUB_RC:-0}"
STUB
chmod +x "$TMP_DIR/python"
echo '{}' >"$TMP_DIR/config.json"

run() {
  local work="$TMP_DIR/work-$1"
  shift
  : >"$TMP_DIR/stub.out"
  env -i PATH="$PATH" IWB_WORK_DIR="$work" IWB_CONTROLLER_PYTHON="$TMP_DIR/python" \
    STUB_OUT="$TMP_DIR/stub.out" "$@" bash "$RUN" "$TMP_DIR/config.json"
}

# 1. Runs the controller on the config with local results and the default connection; records its PID.
run args || fail "run failed"
grep -qx -- "args=-m iwb run --config $TMP_DIR/config.json --results-dir $TMP_DIR/work-args/results --connection default" \
  "$TMP_DIR/stub.out" || fail "controller args wrong: $(cat "$TMP_DIR/stub.out")"
[[ -s "$TMP_DIR/work-args/controller.pid" ]] || fail "controller.pid not written"

# 2. The controller's exit code is returned and results are copied to IWB_RESULTS_COPY_DIR.
mkdir -p "$TMP_DIR/stage"
rc=0
run copy IWB_RESULTS_COPY_DIR="$TMP_DIR/stage" STUB_RC=20 || rc=$?
(( rc == 20 )) || fail "controller exit 20 returned as $rc"
[[ -f "$TMP_DIR/stage/RUN_1/result.json" ]] || fail "results not copied"

# 3. A failed copy turns success into exit 1.
touch "$TMP_DIR/not-a-dir"
rc=0
run badcopy IWB_RESULTS_COPY_DIR="$TMP_DIR/not-a-dir" >"$TMP_DIR/out" 2>/dev/null || rc=$?
(( rc == 1 )) || fail "failed copy exited $rc, want 1"
grep -q results_copy_failed "$TMP_DIR/out" || fail "copy failure not reported"

# 4. A stop before the controller starts: it is never started.
mkdir -p "$TMP_DIR/work-early" && touch "$TMP_DIR/work-early/stop"
rc=0
run early >/dev/null || rc=$?
(( rc == 143 )) || fail "early stop exited $rc, want 143"
[[ ! -s "$TMP_DIR/stub.out" ]] || fail "controller started after an early stop"

# 5. A stop while the controller runs: SIGTERM to the recorded PID ends it with 143.
rc=0
run late STUB_SLEEP=30 &
runner=$!
for _ in $(seq 100); do [[ -s "$TMP_DIR/work-late/controller.pid" ]] && break; sleep 0.1; done
touch "$TMP_DIR/work-late/stop"
kill -TERM "$(cat "$TMP_DIR/work-late/controller.pid")"
wait "$runner" || rc=$?
(( rc == 143 )) || fail "SIGTERM to the controller PID exited $rc, want 143"

echo "Controller run contract passed."
