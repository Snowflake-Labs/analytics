# Running the benchmark in a Cortex Sandbox

`launch.py` runs one benchmark in a [Cortex Sandbox](https://docs.snowflake.com/en/LIMITEDACCESS/developer-guide/cortex-sandboxes/overview)
(private preview, enabled per account) with a single command. It needs no compute pool, no
service and no image release: the sandbox runs the API, Locust and controller sources from
this checkout.

The sandbox runs as **your** Snowflake identity, with the role you pass. The benchmark can
only read what that role can read.

## Quick start

Needs Python 3.11+ and a `connections.toml` entry for an account with Cortex Sandboxes
enabled. Only PAT authentication (`authenticator = "PROGRAMMATIC_ACCESS_TOKEN"` or a `token`
field) has been tested. From the repository root (`run.json` can live anywhere):

```bash
cd interactive-benchmark/sandbox
python3 -m venv .venv
.venv/bin/pip install "snowflake-sandbox-python==0.2.2a4"
.venv/bin/python launch.py --config run.json --connection my-conn \
  [--role MY_ROLE] [--results-stage MY_DB.MY_SCHEMA.IWB_RESULTS]
```

Pin the SDK: it is published only as pre-releases, and the launcher works around 0.2.2a4
behaviour (see [How it works](#how-it-works)).

## Config

`run.json` follows [`iwb-config.schema.json`](../spcs-images/controller/iwb/iwb-config.schema.json):

```json
{
  "schema_version": 1,
  "name": "EU_DASHBOARD",
  "context": {"database": "SALES", "schema": "PUBLIC"},
  "workload": {"queries": [
    {"sql": "select ... where region = 'EU'", "weight_pct": 70},
    {"sql": "select ... where region = 'US'", "weight_pct": 30}
  ]},
  "warehouse": {"create": {"size": "XSMALL", "min_cluster_count": 1, "max_cluster_count": 1}},
  "concurrent_users": 50,
  "run_minutes": 3
}
```

- `workload`: either `queries` (SQL texts with `weight_pct` summing to 100; each may override
  `context`) or `query_ids` (IDs from query history: plain strings for equal weights, or
  `{"query_id": ..., "weight_pct": ...}` for every ID, summing to 100).
- `warehouse`: `create` (dropped when the run ends) or `{"existing": "MY_IW"}` (never altered
  or dropped; if it was suspended, it is resumed for the run and suspended again afterwards).
- `fallback_warehouse` (optional): a standard warehouse that re-runs statements hitting the
  5 s interactive timeout. Without it those statements fail and count against the 1% failure
  gate, which is what you want when measuring the interactive warehouse alone.
- `name` (optional; a letter, then up to 30 letters, digits or `_`) becomes the uppercased run
  id prefix, and so the results folder name; anything else falls back to `IWB`.
- Each Locust user waits 0.5–1.5 s between requests, so `concurrent_users` is simulated
  dashboard users, not concurrent queries: each user sends about one query per second,
  so 50 users gave about 47 queries/s.

## Output and results

The launcher prints one JSON event per line (`"iwb": "event"`, with `step` and `status`) as
the run progresses, then `result.json`:

| Field | Meaning |
|---|---|
| `outcome` | `PASS`, `BENCHMARK_FAIL`, `BASELINE_FAIL` or `API_NOT_READY` |
| `baseline` | Locust against a no-op API endpoint for 1 min. It checks the sandbox can drive the load (gate: failures ≤ 1%, p99 ≤ 500 ms); it says nothing about the warehouse |
| `client` | Locust's view of the benchmark: requests, failures, req/s, latency percentiles. Includes the API and network overhead |
| `server` | The same window from `INFORMATION_SCHEMA.QUERY_HISTORY_BY_WAREHOUSE`, filtered to the run's query tag: `n`, `n_failed`, `n_fallback`, percentiles of total elapsed time, `p95_interactive_only_ms`, average compile/exec/queue time and `clusters_used`. **Use these for warehouse latency** |
| `window` | Epoch seconds of the measured window (full load only, ramp-up excluded) |
| `workload`, `warehouse` | The resolved queries (ids `q01`…, weights, context) and the warehouse used |

`server` counts only statements that ended inside `window`. `client` comes from Locust's
stats CSV, which can trail its final console summary (in `locust_run.log`) by a fraction of
a second of requests, so `client` may be slightly below `server`. There is no per-query
breakdown: query `INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER` with the run id as `QUERY_TAG`
while it is fresh, split by time, since its `RESULT_LIMIT` of 10,000 applies before your
filter. `QUERY_HISTORY_BY_WAREHOUSE` returns nothing once a created warehouse is dropped.

With `--results-stage`, every file (Locust CSVs and HTML reports, API and Locust logs,
`workload.json`, `result.json`) is copied to `@<stage>/<run_id>/` when the run ends. The
`result` path in the final event is inside the sandbox; use the stage path:

```sql
LIST @MY_DB.MY_SCHEMA.IWB_RESULTS/EU_DASHBOARD_20261008185707_AE77/;
GET @MY_DB.MY_SCHEMA.IWB_RESULTS/EU_DASHBOARD_20261008185707_AE77/ file:///tmp/results/;
```

`LIST` prints paths prefixed with the lowercased stage name (`iwb_results/<run_id>/...`); that
prefix is not a folder. Each run also leaves an empty-named 16-byte object for the folder,
which `GET` downloads as a `.part#...` file; ignore it.

### Exit codes

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

## Privileges for the sandbox role

- Creating a sandbox: the required privilege is not documented yet; `SYSADMIN` works.
- SELECT on every table the queries read, plus USAGE on their database/schema. Grant
  interactive tables explicitly: `GRANT SELECT ON ALL TABLES` does not cover them.
- `warehouse.existing`: USAGE on that interactive warehouse.
- `warehouse.create`: `CREATE WAREHOUSE` on the account.
- `fallback_warehouse`: USAGE on it.
- `workload.query_ids`: the queries must be visible to the role. Without SNOWFLAKE database
  access, only the role's own queries among its latest 10,000 (within 7 days) can be found.
- `--results-stage`: READ and WRITE on the stage.

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
| `RuntimeError: greenlet is being finalized` in `locust.log` | Harmless gevent noise at Locust shutdown |

## Timing and sizing

- A 3-minute run takes about 7 minutes: sandbox and install 10 s–1 min, warehouse creation ~2 min,
  warm-up, the 1-minute baseline, ramp-up plus `run_minutes`, then measurement and teardown.
- The API and Locust share one container. The launcher picks the memory tier from
  `concurrent_users`: 4g (2 cores) up to 50, 16g (4 cores) up to 200, 32g (6 cores) above.
  Verified up to 200 users on 16g (baseline p99 2 ms at 200 req/s); the baseline gate fails
  (exit 40) if the sandbox cannot keep up.
- The launcher's deadline is the controller's worst case (`spcs-images/controller/iwb/budget.py`:
  two 15-minute warehouse waits, the Locust timeout, setup and teardown) plus 10 minutes for the
  install, so the controller always times out and cleans up first. `idle_suspend` is that plus 5
  minutes.
- If the launcher is stopped or hits its deadline, it sends SIGTERM to the controller and waits
  up to 5 minutes for teardown (which drops a created warehouse) before terminating the sandbox.

## How it works

1. The launcher creates a sandbox, uploads a zipped bundle (`spcs-images/{api,locust,controller}` plus `bootstrap.sh` and your config) and unpacks it.
2. It starts `bootstrap.sh` detached. The script installs the pinned dependencies from the `uv.lock` files (pypi.org only; the sandbox cannot reach internal mirrors).
3. `bootstrap.sh` then runs `python -m iwb run --config config.json`, the same controller the image runs with `BENCHMARK_ROLE=controller`.
4. The launcher relays events from the run log until the controller exits, prints `result.json`, then terminates the sandbox.

The SDK's `code=`/`command=` option is not used. In `snowflake-sandbox-python` 0.2.2a4 it drops `role` and `idle_suspend`, runs the command before the code is extracted, and keeps the sandbox running after the command exits, so `poll()` never returns.

## Platform notes

Verified on a preprod account (`sandbox-base:1.0.4`, Python 3.11, SDK 0.2.2a4) with 10- to 200-user runs that created and dropped their warehouse:

- The platform-rendered `default` connection works with the Python connector, as the `--role` role.
- `exec()` raises on a non-zero exit, so the launcher detects completion from an exit-code file, not a PID.
- Stage mounts do not support `truncate()`, which Locust's CSV writer needs. `bootstrap.sh` writes results to local disk and copies them to `IWB_RESULTS_DIR` at the end. The SDK's file API also cannot read mount paths.
- Client-side latency includes the sandbox's network path. One run saw a 13 s stall across all users while no query took over 326 ms on the server. Prefer the `server` metrics, and repeat a run if client p99 is far above server p99.
