# Getting Started with the Interactive Benchmark Skill

This skill benchmarks any SQL query against a Snowflake Interactive Warehouse under concurrent load. It deploys a FastAPI server and [Locust](https://locust.io) load generator to Snowpark Container Services (SPCS), measures latency percentiles (P50/P95/P99), and auto-scales the warehouse until the P95 target is met.

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

## 3. Verify the approved image

The benchmark uses one immutable approved image from
`SNOWFLAKE.IMAGES.SNOWFLAKE_IMAGES` for both API and Locust roles.
`deploy.sh` checks the exact configured image and tag before creating compute
pools. It also verifies that the service-owner role can use both warehouses
and execute the benchmark queries against the interactive schema. No Docker setup
is required.

To check manually that the image exists and your role can see it, list the
images in the system registry and filter for the benchmark image (the tag is
`IMAGE_TAG` in `spcs-images/.env`; see the README for the current version):

```sql
USE ROLE SYSADMIN;  -- or the role you deploy with

-- Show the repository and your privileges on it
SHOW IMAGE REPOSITORIES LIKE 'SNOWFLAKE_IMAGES' IN SCHEMA SNOWFLAKE.IMAGES;

-- List images and keep only the benchmark ones
SHOW IMAGES IN IMAGE REPOSITORY SNOWFLAKE.IMAGES.SNOWFLAKE_IMAGES;
SELECT "image_name", "tags", "image_path"
FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
WHERE "image_path" ILIKE '%interactive-benchmark%';
```

If the benchmark image and the tag you expect are returned, your role has access.
If the query fails or returns no rows, ask an administrator to grant your role
read access to the repository.

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

The skill takes over from here. It will:

1. **Collect inputs** -- ask you to confirm the database, schema, connection name, P95 latency goal, concurrency level, warehouse size limits, and benchmark name.
1. **Validate suitability** -- run the query on both standard and interactive warehouses to confirm the interactive warehouse provides a meaningful speedup.
1. **Check permissions** -- check that permission are in place to run the benchmark
1. **Deploy SPCS infrastructure** -- upload benchmark queries to a Snowflake stage, create compute pools, and deploy the API and Locust services (using pre-built container images).
1. **Run the benchmark** -- Locust auto-starts inside SPCS, runs a baseline test, then the actual load test at your target concurrency.
1. **Auto-escalate** -- if the P95 goal is not met, the skill scales the warehouse (out or up) and re-runs, repeating until the goal is met or limits are reached.
1. **Generate a report** -- produce an HTML report with latency percentiles, throughput, and warehouse configuration for each iteration.
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
- `locust-run-N.txt` -- raw Locust output for each iteration
