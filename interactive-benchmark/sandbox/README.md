# Running the benchmark in a Cortex Sandbox

`launch.py` runs one benchmark in a [Cortex Sandbox](https://docs.snowflake.com/en/LIMITEDACCESS/developer-guide/cortex-sandboxes/overview)
(private preview, enabled per account) with a single command. It needs no compute pool and no
service. With `--image` the sandbox runs the released benchmark image, which is how Snowsight
runs it (see [Running the benchmark image](#running-the-benchmark-image)); without it, the
sandbox runs the API, Locust and controller sources from this checkout, so it also works
before an image release.

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
  [--role MY_ROLE] [--results-stage MY_DB.MY_SCHEMA.IWB_RESULTS] \
  [--image /snowflake/images/snowflake_images/interactive-analytics/interactive-benchmark:<tag>]
```

`--image` needs an image with `controller/run.sh` (0.3.0 or later).

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
- `lookup_warehouse` (optional, `query_ids` only): query IDs are looked up through the GS
  monitoring API, which needs no warehouse but only covers job retention (14 days by default).
  To also search `SNOWFLAKE.ACCOUNT_USAGE` (365 days, needs access to it), name a standard
  warehouse here.
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
| `error`, `teardown_error` | Why the run failed, and why teardown failed: a created warehouse not dropped, or an existing one not suspended again (only when that happened) |
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
| 40 | Infrastructure (API not ready or exited during the load, baseline gate failed) |
| 1 | Unexpected error (the `failed` event of the step it stopped, and `error` in `result.json`), a failed results copy (`results_copy_failed`), or a launcher error |
| 64, 65 | `bootstrap.sh` or `run.sh` usage error, or an incomplete bundle |
| 143, 130 | Launcher stopped by SIGTERM or SIGINT; the controller tore down first (a deadline exits 1 after the same teardown) |

When the run exits without the controller's final event, or with a different code, the launcher also prints the tail of the run log.

## Privileges for the sandbox role

- Creating a sandbox: the required privilege is not documented yet; `SYSADMIN` works.
- SELECT on every table the queries read, plus USAGE on their database/schema. Grant
  interactive tables explicitly: `GRANT SELECT ON ALL TABLES` does not cover them.
- `warehouse.existing`: USAGE on that interactive warehouse, and OPERATE if it may be suspended
  (it is then resumed for the run).
- `warehouse.create`: `CREATE WAREHOUSE` on the account.
- `fallback_warehouse`, `lookup_warehouse`: USAGE on them.
- `workload.query_ids`: your own queries (any role), or MONITOR on the user or the query's
  warehouse.
- `--results-stage`: READ and WRITE on the stage.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Exception from `Sandbox.create` (auth error or 403) | Cortex Sandboxes are not enabled for the account, or the role cannot create sandboxes |
| `run exited` after a uv or pip 403 or download error | The sandbox cannot reach pypi.org (without `--image` only) |
| Exit 30, `does not compile for this role` | Missing USAGE or SELECT on an object the query reads |
| Exit 30, `Query IDs not found` | Not visible to you (not your own, no MONITOR), or older than job retention (14 days by default): set `lookup_warehouse` to search ACCOUNT_USAGE |
| Exit 30, interactive table not attached | `warehouse.existing` does not have that table attached |
| Exit 40 | The baseline failed (the sandbox cannot drive that many users), or the API never became ready |
| Exit 20 | More than 1% of queries failed, for example on the 5 s interactive timeout with no `fallback_warehouse` |
| An `IWB_*_IW` warehouse is left behind | Teardown failed: the `TEARDOWN failed` event and `teardown_error` in `result.json` name it. Later runs that create a warehouse drop it once its expiry (the run's worst-case duration plus an hour) has passed |
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
  install, so the controller normally times out and cleans up first. `idle_suspend` is that plus 5
  minutes.
- If the launcher is stopped or hits its deadline, it sends SIGTERM to the controller and waits
  up to 5 minutes for teardown (which drops a created warehouse) before terminating the sandbox.

## Running the benchmark image

The image's controller role runs one benchmark from a config file. This is the contract for a
host such as Snowsight. Use image `/snowflake/images/snowflake_images/interactive-analytics/interactive-benchmark:<tag>`
(0.3.0 or later), with the leading `/`: `SHOW IMAGES` prints the path without it.

- **Start**: write the config to a file such as `/var/tmp/iwb/config.json`, then run
  `benchmark-entrypoint run --config /var/tmp/iwb/config.json` with `BENCHMARK_ROLE=controller`,
  as the user's role. Give it the memory tier and `idle_suspend` from [Timing and sizing](#timing-and-sizing).
- **Connection**: the controller connects with the session token at `/snowflake/session/token`
  (or `SNOWFLAKE_TOKEN_FILE_PATH`), using `SNOWFLAKE_ACCOUNT` and the host from `SNOWFLAKE_HOST`
  or `SNOWFLAKE_ACCOUNT_URL`. It uses the account host as given; the hyphenated form is rejected
  by the egress proxy for account names with underscores. Without a readable token, the account,
  or a host, it uses the connection named by `--connection` (default `default`) in
  `$SNOWFLAKE_HOME/connections.toml`. `SNOWFLAKE_ROLE`, if set, is the role.
- **Progress**: stdout has one JSON event per line, `{"iwb": "event", "seq": n, "step": ..., "status": ..., "detail": {...}}`,
  with `seq` counting from 1. Skip other lines. Only the last event, `RUN` with
  `detail.exit_code` and `detail.result` (the path of `result.json`), or the process exiting,
  ends the run. A step's `failed` event does not: teardown, which drops a created warehouse,
  still follows, so keep the sandbox until then.
- **Result**: `$IWB_RESULTS_DIR/<run_id>/result.json` (default `/var/tmp/iwb/results`). The
  process's exit code is in [Exit codes](#exit-codes).
- **Cancel**: send SIGTERM to that process and give it up to 5 minutes to exit before removing
  the sandbox. That lets it drop a warehouse it created. Further signals are ignored while it
  tears down.

`launch.py --image` instead drives the image through `exec` calls. It writes the config, starts
`/app/controller/run.sh /var/tmp/iwb/config.json` detached with its output in
`/var/tmp/iwb/run.log` and its exit code in `/var/tmp/iwb/run.rc`, and polls both. To cancel,
it creates `/var/tmp/iwb/stop` and sends SIGTERM to the PID in `/var/tmp/iwb/controller.pid`.
`run.sh` checks the stop file once it has written the PID, so a cancel that arrives before
the controller has started still takes effect. With `--results-stage`, `run.sh`
copies the results to the stage mount (`IWB_RESULTS_COPY_DIR`) at the end.

## How it works

Without `--image`:

1. The launcher creates a sandbox, uploads a zipped bundle (`spcs-images/{api,locust,controller}` plus `bootstrap.sh` and your config) and unpacks it.
2. It starts `bootstrap.sh` detached. The script installs the pinned dependencies from the `uv.lock` files (pypi.org only; the sandbox cannot reach internal mirrors).
3. `bootstrap.sh` then runs `controller/run.sh config.json`, which starts the same controller as the image's `controller` role.
4. The launcher relays events from the run log until the controller exits, prints `result.json`, then terminates the sandbox.

With `--image`, steps 1 to 3 are those of [Running the benchmark image](#running-the-benchmark-image).

The SDK's `code=`/`command=` option is not used. In `snowflake-sandbox-python` 0.2.2a4 it drops `role` and `idle_suspend`, runs the command before the code is extracted, and keeps the sandbox running after the command exits, so `poll()` never returns.

## Platform notes

Verified on a preprod account (`sandbox-base:1.0.4`, Python 3.11, SDK 0.2.2a4) with 10- to 200-user runs that created and dropped their warehouse:

- The `default` connection the default image renders uses the hyphenated account host, which the
  egress proxy rejects (HTTP 403) for account names with underscores, so the controller connects
  with the session token instead (verified on preprod8, account `AL_BENCHMARK_PP8`).
- On the benchmark image the sandbox runs as root and renders no `connections.toml`. `exec` needs
  `python3` and `bash`, which the image has.
- `exec()` raises on a non-zero exit, so the launcher detects completion from an exit-code file, not a PID.
- Stage mounts do not support `truncate()`, which Locust's CSV writer needs. `run.sh` writes results to local disk and copies them to `IWB_RESULTS_COPY_DIR` at the end. The SDK's file API also cannot read mount paths.
- Client-side latency includes the sandbox's network path. One run saw a 13 s stall across all users while no query took over 326 ms on the server. Prefer the `server` metrics, and repeat a run if client p99 is far above server p99.
