#!/usr/bin/env bash
# Contract tests for write-report.sh against a mock snow.

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/queries" "$TMP_DIR/report"
echo "SELECT 1;" >"$TMP_DIR/queries/a-query.sql"
echo "SELECT 2;" >"$TMP_DIR/queries/b-query.sql"
echo "SELECT 3;" >"$TMP_DIR/queries/c-query.sql"

cat >"$TMP_DIR/config.env" <<EOF
CONNECTION=test
ROLE=TEST_ROLE
DEPLOY_WAREHOUSE=DEPLOY_WH
SOLUTION_NAME=TEST
DB=TEST_DB
SCHEMA=SPCS
IMAGE_DB=SNOWFLAKE
IMAGE_SCHEMA=IMAGES
IMAGE_REPO=SNOWFLAKE_IMAGES
BENCHMARK_IMAGE=interactive-analytics/interactive-benchmark
IMAGE_TAG=0.3.0
BENCHMARK_IMAGE_ARCH=amd64
INTERACTIVE_WAREHOUSE=TEST_INT_WH
LOCUST_USERS=20
LOCUST_RUN_TIME=3m
BENCHMARK_QUERY_DIR=$TMP_DIR/queries
EOF

# Answers by the statement on stdin; records every CALL payload in calls.jsonl.
cat >"$TMP_DIR/snow" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
fixtures="$(dirname "$0")"
[[ "$*" == *"--role TEST_ROLE"* && "$*" == *" -i"* ]] || { echo "unexpected args: $*" >&2; exit 1; }
input="$(cat)"
case "$input" in
  *"SHOW PROCEDURES"*)
    if [[ -f "$fixtures/no-access" ]]; then
      echo "002003 (02000): Schema 'SNOWFLAKE.INTERACTIVE_ANALYTICS' does not exist or not authorized." >&2
      exit 1
    fi
    echo '[[{"name":"WRITE_BENCHMARK_REPORT"}]]' ;;
  *"SHOW WAREHOUSES"*)
    echo '[[{"name":"TEST_INT_WH","size":"2X-Large","min_cluster_count":"1","max_cluster_count":"3"}]]' ;;
  *"GROUPING SETS"*)
    echo '[[],[{"ALL_ROWS":1,"QUERY_HASH":null,"QUERY_TEXT":"SELECT 1;","N":30,"N_OK":29,"P50":10,"P95":20,"P99":30,"MIN_MS":1,"MAX_MS":40,"AVG_MS":12.5},
            {"ALL_ROWS":0,"QUERY_HASH":"H1","QUERY_TEXT":"SELECT 1;","N":20,"N_OK":20,"P50":9,"P95":18,"P99":19,"MIN_MS":1,"MAX_MS":20,"AVG_MS":10},
            {"ALL_ROWS":0,"QUERY_HASH":"H2","QUERY_TEXT":"SELECT 2;","N":10,"N_OK":9,"P50":15,"P95":35,"P99":40,"MIN_MS":5,"MAX_MS":40,"AVG_MS":17.5}]]' ;;
  *"ORDER BY TOTAL_ELAPSED_TIME DESC"*)
    python3 -c 'import json
rows = [{"QUERY_ID": f"x{i}", "QUERY_HASH": "H1" if i % 2 else "H2", "EXECUTION_STATUS": "SUCCESS" if i else "FAILED_WITH_ERROR",
         "START_TIME": "2026-10-19 20:00:00.000 +0000", "END_TIME": "2026-10-19 20:00:01.000 +0000", "TOTAL_ELAPSED_TIME": 100 - i,
         "COMPILATION_TIME": 1, "EXECUTION_TIME": 2, "QUEUED_MS": 0, "BYTES_SCANNED": 3, "ROWS_PRODUCED": 4,
         "ERROR_CODE": None, "ERROR_MESSAGE": None} for i in range(40)]
rows.insert(3, {**rows[3], "QUERY_ID": "unknown", "QUERY_HASH": "OTHER"})
print(json.dumps([[], rows]))' ;;
  *"CALL "*)
    python3 -c 'import json, re, sys
body = re.search(r"PARSE_JSON\(\$\$(.*)\$\$\)", sys.stdin.read(), re.S).group(1)
open(sys.argv[1], "a").write(json.dumps(json.loads(body)) + "\n")
print(json.dumps([[], [{"WRITE_BENCHMARK_REPORT": {"result": "UPDATED"}}]]))' "$fixtures/calls.jsonl" <<<"$input" ;;
  *) echo "unexpected statement: $input" >&2; exit 1 ;;
esac
EOF
chmod +x "$TMP_DIR/snow"

run() {
  PATH="$TMP_DIR:$PATH" BENCHMARK_CONFIG_ENV="$TMP_DIR/config.env" "$SCRIPTS_DIR/write-report.sh" "$TMP_DIR/report" "$@"
}
fail() { echo "FAIL: $*" >&2; exit 1; }
last() { tail -1 "$TMP_DIR/calls.jsonl" | python3 -c "import json, sys; d = json.load(sys.stdin); print($1)"; }

run created --warehouse-created --description "goal P95 <= 1 s" >/dev/null
[[ "$(last 'd["status"], d["name"], d["requested_concurrency"], d["runtime_seconds"], d["tests"]')" == "CREATED TEST 20 180 []" ]] \
  || fail "created payload: $(last 'd')"
[[ "$(last '[(q["query_id"], q["weight_percent"]) for q in d["queries"]]')" == \
   "[('a-query', 33.34), ('b-query', 33.33), ('c-query', 33.33)]" ]] || fail "query weights must sum to 100"
[[ "$(last 'd["queries"][1]["query_text"], d["description"]')" == "SELECT 2; goal P95 <= 1 s" ]] || fail "query text"

run running >/dev/null
[[ "$(last 'd["status"]')" == "RUNNING" ]] || fail "running"

run iteration 1 1792440000 1792440180 >/dev/null
[[ "$(last 'd["status"], len(d["tests"])')" == "RUNNING 1" ]] || fail "iteration keeps the run RUNNING"
test_fields='t["test_id"], t["attempt_number"], t["warehouse_size"], t["min_cluster_count"], t["max_cluster_count"], t["warehouse_created_for_run"]'
[[ "$(last "(lambda t: ($test_fields))(d['tests'][0])")" == "('TEST_T1', 1, 'XXLARGE', 1, 3, True)" ]] \
  || fail "test row: $(last 'd["tests"][0]')"
[[ "$(last '[(m["query_id"], m["sample_count"], m["throughput_qps"]) for m in d["tests"][0]["metrics"]]')" == \
   "[(None, 30, 0.16666666666666666), ('a-query', 20, 0.1111111111111111), ('b-query', 10, 0.05555555555555555)]" ]] \
  || fail "metrics: $(last 'd["tests"][0]["metrics"]')"
[[ "$(last 'len(d["tests"][0]["executions"]), "unknown" in [e["execution_id"] for e in d["tests"][0]["executions"]]')" == "25 False" ]] \
  || fail "executions keep the 25 slowest attributed statements"
[[ "$(last 'd["tests"][0]["executions"][0]["status"], d["tests"][0]["started_at"]')" == "FAILED 2026-10-19T20:00:00+00:00" ]] \
  || fail "execution status / test start"

run iteration 2 1792440600 1792440780 >/dev/null
run iteration 2 1792440600 1792440780 >/dev/null
[[ "$(last '[t["test_id"] for t in d["tests"]]')" == "['TEST_T1', 'TEST_T2']" ]] || fail "re-running an iteration replaces it"

run final COMPLETED >/dev/null
[[ "$(last 'd["status"], d["result"], d["error_code"], len(d["tests"]), d["completed_at"] is not None')" == \
   "COMPLETED SUCCESS None 2 True" ]] || fail "final"
run final ERROR --message "deploy failed" >/dev/null
[[ "$(last 'd["status"], d["result"], d["error_code"], d["error_message"]')" == "ERROR None ERROR deploy failed" ]] \
  || fail "final error"
if run final COMPLETED MAYBE >/dev/null 2>&1; then fail "an unknown result was accepted"; fi

calls_before="$(wc -l <"$TMP_DIR/calls.jsonl")"
touch "$TMP_DIR/no-access"
output="$(run created)"
grep -q "Grant SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE to TEST_ROLE" <<<"$output" || fail "skip message: $output"
run running >/dev/null
run final COMPLETED >/dev/null
[[ "$(wc -l <"$TMP_DIR/calls.jsonl")" == "$calls_before" ]] || fail "a skipped report still called the procedure"

rm "$TMP_DIR/report/benchmark-report-state.json"
if run running >/dev/null 2>&1; then fail "running without created was accepted"; fi

echo "write-report contract passed."
