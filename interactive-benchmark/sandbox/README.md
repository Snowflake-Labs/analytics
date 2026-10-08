# Running the benchmark in a Cortex Sandbox

`launch.py` runs one benchmark in a [Cortex Sandbox](https://docs.snowflake.com/en/LIMITEDACCESS/developer-guide/cortex-sandboxes/overview)
(private preview, enabled per account). No compute pool, service, or image release is needed:

1. The launcher creates a sandbox, uploads a zipped bundle (`spcs-images/{api,locust,controller}` plus `bootstrap.sh` and your config) and unpacks it.
2. It starts `bootstrap.sh` detached. The script installs the pinned dependencies from the `uv.lock` files (pypi.org only; the sandbox cannot reach internal mirrors).
3. `bootstrap.sh` then runs `python -m iwb run --config config.json`, the same controller the image runs with `BENCHMARK_ROLE=controller`.
4. The launcher relays events from the run log until the final `RUN` event, prints `result.json`, then terminates the sandbox.

The SDK's `code=`/`command=` option is not used. In `snowflake-sandbox-python` 0.2.2a4 it drops `role` and `idle_suspend`, runs the command before the code is extracted, and keeps the sandbox running after the command exits, so `poll()` never returns.

The sandbox runs as **your** Snowflake identity, with the role you pass. The benchmark can
only read what that role can read.

## Usage

Needs Python 3.11+ and a `connections.toml` entry for an account with Cortex Sandboxes enabled. Only PAT authentication has been tested.

```bash
python3 -m venv .venv
.venv/bin/pip install "snowflake-sandbox-python==0.2.2a4"
.venv/bin/python launch.py --config run.json --connection my-conn \
  [--role MY_ROLE] [--results-stage MY_DB.MY_SCHEMA.IWB_RESULTS]
```

Pin the SDK: it is published only as pre-releases, and the launcher works around 0.2.2a4 behaviour (see above).

`run.json` follows [`iwb-config.schema.json`](../spcs-images/controller/iwb/iwb-config.schema.json):

```json
{
  "schema_version": 1,
  "context": {"database": "SALES", "schema": "PUBLIC"},
  "workload": {"queries": [
    {"sql": "select ... where region = 'EU'", "weight_pct": 70},
    {"sql": "select ... where region = 'US'", "weight_pct": 30}
  ]},
  "warehouse": {"create": {"size": "SMALL", "min_cluster_count": 1, "max_cluster_count": 4}},
  "concurrent_users": 50,
  "run_minutes": 10
}
```

The launcher prints the controller's progress events (one JSON object per line, `"iwb": "event"`), then `result.json`. It exits with the controller's exit code:

| Code | Meaning |
|---|---|
| 0 | Completed |
| 20 | Benchmark verdict FAIL (no queries ran, or failures above 1%); numbers are not valid |
| 30 | Invalid config, or missing access (the event says what) |
| 40 | Infrastructure (API not ready, baseline gate failed) |
| 1 | Unexpected error (see the `RUN failed` event), a failed results copy (`results_copy_failed`), or a launcher error |
| 64, 65 | `bootstrap.sh` usage error or incomplete bundle |
| 143, 130 | Launcher stopped by SIGTERM or SIGINT; the controller tore down first (a deadline exits 1 after the same teardown) |

When bootstrap exits without the controller's final event, or with a different code, the launcher also prints the tail of the run log.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Exception from `Sandbox.create` (auth error or 403) | Cortex Sandboxes are not enabled for the account, or the role cannot create sandboxes |
| `bootstrap exited` after a uv or pip 403 or download error | The sandbox cannot reach pypi.org |
| Exit 30, `does not compile for this role` | Missing USAGE or SELECT on an object the query reads |
| Exit 30, `Query IDs not found` | The role cannot see the queries: not its own, older than 7 days, or no SNOWFLAKE database access |
| Exit 30, interactive table not attached | `warehouse.existing` does not have that table attached |
| Exit 40 | The baseline failed (the sandbox cannot drive that many users), or the API never became ready |
| Exit 20 | More than 1% of queries failed, for example on the 5 s interactive timeout with no `fallback_warehouse` |
| An `IWB_*_IW` warehouse is left behind | Teardown failed; the event names it. Later runs that create a warehouse drop it once its expiry (run length + 3 h) has passed |

## Privileges for the sandbox role

- SELECT on every table the queries read, plus USAGE on their database/schema.
- `warehouse.existing`: USAGE on that interactive warehouse.
- `warehouse.create`: `CREATE WAREHOUSE` on the account. The warehouse is dropped when the run ends.
- `workload.query_ids`: the queries must be visible to the role. Own queries from the last 7 days work; older ones need SNOWFLAKE database access.

## Sizing

- The API and Locust share one container. The launcher picks the memory tier from `concurrent_users`: 4g (2 cores) up to 50, 16g (4 cores) up to 200, 32g (6 cores) above.
- The launcher's deadline is the run length plus 2 s per user plus 60 minutes, which is longer than the controller's own timeouts. `idle_suspend` is that plus 5 minutes. A sandbox otherwise suspends after 10 minutes without inbound traffic; polling counts as traffic.
- If the launcher is stopped or hits its deadline, it sends SIGTERM to the controller and waits up to 5 minutes for teardown (which drops a created warehouse) before terminating the sandbox.

## Platform notes

Verified on a preprod account (`sandbox-base:1.0.4`, Python 3.11, SDK 0.2.2a4) with a 2-query, 10-user, 2-minute run that created and dropped its warehouse:

- The platform-rendered `default` connection works with the Python connector, as the `--role` role.
- `exec()` raises on a non-zero exit, so the launcher detects completion from an exit-code file, not a PID.
- Stage mounts do not support `truncate()`, which Locust's CSV writer needs. `bootstrap.sh` writes results to local disk and copies them to `IWB_RESULTS_DIR` at the end. The SDK's file API also cannot read mount paths.
- Client-side latency includes the sandbox's network path. One run saw a 13 s stall across all users while no query took over 326 ms on the server. Prefer the `server` metrics, and repeat a run if client p99 is far above server p99.
