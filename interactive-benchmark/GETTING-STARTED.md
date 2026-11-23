# Getting Started with the Interactive Benchmark Skill

This skill benchmarks any SQL query against a Snowflake Interactive Warehouse under concurrent load. It deploys a FastAPI server and [Locust](https://locust.io) load generator to Snowpark Container Services (SPCS), and measures latency percentiles (P50/P95/P99) and throughput. It supports two benchmark modes: a **latency target** mode that auto-scales the warehouse until a P95 goal is met, and a **maximum concurrency** mode that finds the requests per second (RPS, a.k.a. QPS) a fixed warehouse configuration can sustain.

For an overview presentation see the [slide deck](https://snowflake-labs.github.io/analytics/interactive-benchmark/slide-deck/index.html), and for an example of the generated output see the [sample report](https://snowflake-labs.github.io/analytics/interactive-benchmark/sample-report/benchmark-report.html).

## 1. Install prerequisites

Starting with [Cortex Code (CoCo) CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli) **v1.2.18** and [CoCo Desktop](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop) **v1.22.1**, the `interactive-benchmark` skill is available natively — there is no need to clone this repository. Just open CoCo CLI or CoCo Desktop and invoke the skill directly.

### Required tools

| Tool | Purpose | Install |
|------|---------|---------|
| **Cortex Code CLI** **v1.2.18+** or **CoCo Desktop** **v1.22.1+** | Runs the skill (ships built-in) | [CLI docs](https://docs.snowflake.com/en/user-guide/ui-snowsight/cortex-code), [Desktop download](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop) |
| **`snow` CLI** | Snowflake CLI for SPCS operations | `pip install snowflake-cli` or `brew install snowflake-cli` |
| **Python 3** | Validates image metadata returned by Snowflake | [python.org](https://www.python.org/downloads/) |
| **`envsubst`** | Renders YAML specs from templates | Comes with `gettext` (`brew install gettext` on macOS) |

## 2. Configure a Snowflake connection

Make sure you have a connection in `~/.snowflake/connections.toml` with a role that can create the benchmark database/schema/stage, warehouses, compute pools,
services, and public service endpoints; use `DEPLOY_WAREHOUSE`; and read the approved System Registry image. `ACCOUNTADMIN` or `SYSADMIN` with appropriate
grants will work. 

```toml
# ~/.snowflake/connections.toml
[myconn]
account = "myorg-myaccount"
user = "myuser"
authenticator = "externalbrowser"
role = "SYSADMIN"
warehouse = "COMPUTE_WH"
```

Verify the connection:

```bash
snow connection test -c myconn
```

## 3. Choose a benchmark mode

| | **Latency target** | **Maximum concurrency (RPS / QPS)** |
|---|---|---|
| Use it to | Check that a query meets a P95 goal at N users | Find the peak requests/sec a warehouse configuration sustains |
| Trigger phrases | "P95 under 1 s", "50 users within 2 s" | "max QPS/RPS", "peak throughput", "stress to the limit", "think time 0" |
| Think time | 0.5-1.5 s (default) | 0 |
| Warehouse | Scales up/out automatically within your limits | Fixed size and cluster count, never scaled |
| Result | P95 vs goal | Sustained requests/sec (plus P50/P95/P99 at that load) |

RPS and QPS are the same thing: each request runs one query. Never mixed in one run; the skill infers the mode from your prompt and asks if unclear.

## 4. Run the benchmark

In the Cortex Code chat panel, type something like:

```text
$interactive-benchmark Benchmark this query on an interactive warehouse with 50 concurrent users and a P95 goal of 1 second:

SELECT l_returnflag, l_linestatus,
        SUM(l_quantity)       AS sum_qty,
        SUM(l_extendedprice)  AS sum_base_price
FROM   lineitem
WHERE  l_shipdate BETWEEN '1998-08-01' AND '1998-08-31'
GROUP  BY l_returnflag, l_linestatus
ORDER  BY l_returnflag, l_linestatus;

tables are in database SNOWFLAKE_SAMPLE_DATA. The tables are in the schema TPCH_SF100.
```

For a maximum concurrency run, ask for throughput instead:

```text
$interactive-benchmark What is the maximum QPS an X-Small interactive warehouse can sustain
for this query with 50 concurrent users and no think time?

<your query>

tables are in database SNOWFLAKE_SAMPLE_DATA, schema TPCH_SF100.
```

The skill takes over from here. It will:

1. **Collect inputs** -- ask you to confirm the benchmark type, database, schema, connection name, concurrency level, warehouse size and cluster count, and benchmark name. Latency runs also ask for the P95 goal, scale-up/out limits and max escalation iterations; maximum concurrency runs instead use the fixed size and cluster count you choose.
1. **Validate suitability** -- run the query on both standard and interactive warehouses to confirm the interactive warehouse provides a meaningful speedup.
1. **Check permissions** -- check that permission are in place to run the benchmark
1. **Deploy SPCS infrastructure** -- upload benchmark queries to a Snowflake stage, create compute pools, and deploy the API and Locust services (using pre-built container images).
1. **Run the benchmark** -- Locust auto-starts inside SPCS, runs a baseline test, then the actual load test at your target concurrency.
1. **Auto-escalate (latency runs only)** -- if the P95 goal is not met, the skill scales the warehouse (out or up) and re-runs, repeating until the goal is met or limits are reached. Maximum concurrency runs skip this step: the warehouse is never resized and the result is the sustained requests/sec.
1. **Generate a report** -- produce an HTML report with latency percentiles, throughput, and warehouse configuration for each iteration. Latency runs use the latency report (P95 vs goal); maximum concurrency runs use the RPS report (sustained requests/sec).
1. **Clean up** -- ask whether to tear down SPCS resources, keep them, or keep just the warehouse.

## What gets created in Snowflake

All object names derive from the `SOLUTION_NAME` you choose (default: `IWB_<YYYYMMDDHH24MI>`, e.g. `IWB_202609091200`):

| Object | Name Pattern |
|--------|-------------|
| Database | `<SOLUTION_NAME>_DB` |
| Interactive warehouse | `<SOLUTION_NAME>_INT_WH` |
| Standard warehouse (fallback) | `<SOLUTION_NAME>_STD_WH` |
| API compute pool | `<SOLUTION_NAME>_API_POOL` |
| Locust compute pool | `<SOLUTION_NAME>_LOCUST_POOL` |
| API service | `BENCHMARK_API` |
| Locust service | `BENCHMARK_LOCUST` |
| Queries stage | `BENCHMARK_QUERIES` |

## Benchmark reports

Each run stores artifacts in `~/.snowflake/interactive-benchmark/reports/<SOLUTION_NAME>/`:

- `progress.json` -- step-by-step progress tracker (13 steps)
- `benchmark-report.html` -- the final HTML report
- `queries/*.sql` -- the exact queries benchmarked
- `locust-run-N.txt` -- raw Locust output for each iteration (one per escalation step in latency runs)
