# Getting Started with the Interactive Benchmark Skill

This skill benchmarks any SQL query against a Snowflake Interactive Warehouse under concurrent load. It deploys a FastAPI server and [Locust](https://locust.io) load generator to Snowpark Container Services (SPCS), measures latency percentiles (P50/P95/P99), and auto-scales the warehouse until the P95 target is met.

To run one benchmark from a JSON config without the skill or SPCS, see [sandbox/README.md](sandbox/README.md) (Cortex Sandbox).

For an overview presentation see the [slide deck](https://snowflake-labs.github.io/interactive-analytics/interactive-benchmark/slide-deck/index.html), and for an example of the generated output see the [sample report](https://snowflake-labs.github.io/interactive-analytics/interactive-benchmark/sample-report/benchmark-report.html).

## 1. Clone the repo

```bash
git clone https://github.com/Snowflake-Labs/interactive-analytics.git
cd interactive-analytics
```

## 2. Install prerequisites

### For running the benchmark (via the CoCo skill)

| Tool | Purpose | Install |
|------|---------|---------|
| **Cortex Code** (Desktop or CLI) | Runs the skill | [Docs](https://docs.snowflake.com/en/user-guide/ui-snowsight/cortex-code) |
| **`snow` CLI** | Snowflake CLI for SPCS operations | `pip install snowflake-cli` or `brew install snowflake-cli` |
| **Python 3** | Validates image metadata returned by Snowflake | [python.org](https://www.python.org/downloads/) |
| **`envsubst`** | Renders YAML specs from templates | Comes with `gettext` (`brew install gettext` on macOS) |

## 3. Configure a Snowflake connection

Make sure you have a connection in `~/.snowflake/connections.toml` with a role
that can create the benchmark database/schema/stage, warehouses, compute pools,
services, and public service endpoints; use `DEPLOY_WAREHOUSE`; and read the
approved System Registry image. `ACCOUNTADMIN` or `SYSADMIN` with appropriate
grants will work. `deploy.sh` verifies image access before creating resources.
Benchmark users do not need Docker or `CREATE IMAGE REPOSITORY`.

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

## 4. Verify the approved image

The benchmark uses one immutable approved image from
`SNOWFLAKE.IMAGES.SNOWFLAKE_IMAGES` for both API and Locust roles.
`deploy.sh` checks the exact configured image and tag before creating compute
pools. It also verifies that the service-owner role can use both warehouses
and execute the benchmark queries against the interactive schema. No Docker setup
is required.

## 5. Open the project in Cortex Code

Open the cloned `interactive-analytics` folder in Cortex Code Desktop, or `cd` into it in Cortex Code CLI. The skill is auto-discovered from `.cortex/skills/interactive-benchmark/SKILL.md`.

## 6. Run the benchmark

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

use database MYDATABASE. The tables are in the schema MYSCHEMA.
```

The skill takes over from here. It will:

1. **Collect inputs** -- ask you to confirm the database, schema, connection name, P95 latency goal, concurrency level, warehouse size limits, and benchmark name.
2. **Create interactive tables** -- invoke the `snowflake-interactive` sub-skill to set up the interactive warehouse and tables for your query.
3. **Validate suitability** -- run the query on both standard and interactive warehouses to confirm the interactive warehouse provides a meaningful speedup.
4. **Deploy SPCS infrastructure** -- upload benchmark queries to a Snowflake stage, create compute pools, and deploy the API and Locust services (using pre-built container images).
5. **Run the benchmark** -- Locust auto-starts inside SPCS, runs a baseline test, then the actual load test at your target concurrency.
6. **Auto-escalate** -- if the P95 goal is not met, the skill scales the warehouse (out or up) and re-runs, repeating until the goal is met or limits are reached.
7. **Generate a report** -- produce an HTML report with latency percentiles, throughput, and warehouse configuration for each iteration.
8. **Clean up** -- ask whether to tear down SPCS resources, keep them, or keep just the warehouse.

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

Each run stores artifacts in `.cortex/skills/interactive-benchmark/benchmark/reports/<SOLUTION_NAME>/`:

- `progress.json` -- step-by-step progress tracker (13 steps)
- `benchmark-report.html` -- the final HTML report
- `locust-run-N.txt` -- raw Locust output for each iteration

## Manual script reference

The scripts in `benchmark/scripts/` can be run directly for operational tasks. They all read configuration from `benchmark/spcs/config.env`.

```bash
SCRIPTS=.cortex/skills/interactive-benchmark/benchmark/scripts

# Check service status
$SCRIPTS/status.sh

# Wait for services to become READY
$SCRIPTS/status.sh --wait

# Get ingress URLs only
$SCRIPTS/status.sh --urls-only

# Resize the interactive warehouse
$SCRIPTS/resize-wh.sh --size LARGE
$SCRIPTS/resize-wh.sh --mcw 3
$SCRIPTS/resize-wh.sh --size MEDIUM --mcw 5

# Tail logs
$SCRIPTS/logs.sh api
$SCRIPTS/logs.sh locust

# Upload new queries and restart the API
$SCRIPTS/update.sh

# Upload .sql files to the queries stage
$SCRIPTS/upload-queries.sh

# List all SPCS resources
$SCRIPTS/list.sh

# Drop benchmark services and compute pools
$SCRIPTS/teardown.sh
```
