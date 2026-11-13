# Interactive Analytics

This repository contains **samples, demos, and benchmarking tools** to help you
get started with Snowflake [**Interactive Analytics**](https://docs.snowflake.com/en/user-guide/interactive) scenarios — including
interactive warehouses, interactive tables, and low-latency, high-concurrency
workloads.

## Getting Started

**Looking for the Interactive Benchmark skill?** Starting with [Cortex Code (CoCo) CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli) **v1.2.18** and [CoCo Desktop](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop) **v1.22.1**, the [`interactive-benchmark` skill](https://docs.snowflake.com/en/user-guide/interactive-skills#the-interactive-benchmark-skill) is available natively — no need to clone this repository. Just open CoCo CLI or CoCo Desktop and invoke the skill directly.

Full documentation available here: [The interactive-benchmark skill](https://docs.snowflake.com/en/user-guide/interactive-skills#the-interactive-benchmark-skill)

Follow the [Getting Started guide](./interactive-benchmark/GETTING-STARTED.md) for prerequisites and a step-by-step walkthrough.

## Contents

### [`benchmarks/`](benchmarks/)

Benchmark harnesses for Snowflake **Interactive Warehouses**, sharing one Python core ([`benchmarks/core/`](benchmarks/core/)) for setup, execution, timing, validation and result storage. Each runs against standard, interactive and iceberg tables, on standard or interactive warehouses, at scale factors 1, 10, 100 and 1000. See [`benchmarks/README.md`](benchmarks/README.md).

- [`benchmarks/tpc-h/`](benchmarks/tpc-h/) — copies TPC-H tables from `SNOWFLAKE_SAMPLE_DATA` into a local benchmark database and runs the 22 standard queries (original and modern rewrites). See [`benchmarks/tpc-h/README.md`](benchmarks/tpc-h/README.md).
- [`benchmarks/ssb/`](benchmarks/ssb/) — Star Schema Benchmark: derives the SSB star schema from the TPC-H sample data and runs the 13 SSB queries (original, modern and flat/denormalized variants). See [`benchmarks/ssb/README.md`](benchmarks/ssb/README.md).

### [`example_apps/`](example_apps/)

Complete sample applications that show interactive warehouses in real workloads.

- [`example_apps/interactive-vs-standard/`](example_apps/interactive-vs-standard/) — "The Concurrency Test": a Snowflake-hosted Next.js app that runs the same retail queries on an XSMALL standard warehouse and an XSMALL interactive warehouse side by side, with 1 to 100 virtual users, and compares p95 latency, errors and queue time. See [`example_apps/interactive-vs-standard/README.md`](example_apps/interactive-vs-standard/README.md).
- [`example_apps/postgres_sync/`](example_apps/postgres_sync/) — the same analytical dashboard (Cube Core + Next.js, 32 tiles) running against Postgres and Snowflake, with a synthetic data generator and a connectivity check. See [`example_apps/postgres_sync/README.md`](example_apps/postgres_sync/README.md).

### [`tpc-h-sample-dashboard/`](tpc-h-sample-dashboard/)

TPC-H interactive dashboard demo. A FastAPI + Chart.js single-page app that visualizes TPC-H data (KPIs, orders over time, segment/region breakdowns) with a Locust load generator for concurrent user simulation. Deployable to SPCS. See [`tpc-h-sample-dashboard/README.md`](tpc-h-sample-dashboard/README.md) for setup and usage.

### [`interactive-benchmark/`](interactive-benchmark/)

A [Cortex Code](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code) skill that benchmarks Snowflake **Interactive Warehouse** performance under concurrent load using any user-provided SQL query. The skill automates the entire workflow — from verifying that your tables and queries are suitable for interactive warehouses to deploying the benchmark infrastructure and generating reports. See [`interactive-benchmark/README.md`](interactive-benchmark/README.md) for sample prompts, repository structure, and SPCS deployment details.

## Cortex Code (CoCo) Skills

This repository includes three **project-level CoCo skills** that automate common workflows through conversational prompts in Cortex Code.

### `interactive-benchmark`

Benchmark **any SQL query** on interactive warehouses under concurrent load.

- Chains the `snowflake-interactive` skill to verify tables and queries are suitable for interactive warehouses
- Deploys the benchmark API and Locust load test to Snowpark Container Services
- Reports whether the query meets its P95 latency goal under load

**Usage:** Ask something like *"benchmark my query on an interactive warehouse"*.

### `interactive-tpch-benchmark`

Set up and run the **TPC-H benchmark locally** against interactive warehouses.

- Creates TPC-H tables at various scale factors (1, 10, 100, 1000)
- Runs the 22 standard queries and collects timing results (JSON + CSV)
- Supports both `original` and `modern` (window functions, QUALIFY) query variants

**Usage:** Ask something like *"set up the TPC-H benchmark at scale 10"* or *"run tpch queries against interactive"*.

### `interactive-tpch-dashboard`

Deploy the **TPC-H dashboard demo** and Locust load test to SPCS.

- Deploys FastAPI + Chart.js dashboard with real-time TPC-H visualizations
- Deploys a Locust load generator for simulating concurrent dashboard users
- Manages SPCS service lifecycle (deploy, status, update, teardown)

**Usage:** Ask something like *"deploy the TPC-H dashboard to SPCS"* or *"check tpch dashboard status"*.

---

More samples and scenarios will be added over time.
