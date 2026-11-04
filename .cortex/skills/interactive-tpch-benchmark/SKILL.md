---
name: interactive-tpch-benchmark
description: "Set up and run the TPC-H benchmark locally against Snowflake Interactive Warehouses vs Standard Warehouses. Use ONLY when the user explicitly mentions TPC-H benchmark, running TPC-H queries, or iwtpch. Do NOT use for the TPC-H dashboard demo (use interactive-tpch-dashboard instead) or for generic benchmarking of user queries (use interactive-benchmark instead). Triggers: tpc-h, tpch, TPC-H benchmark, TPC-H sample, setup tpch, teardown tpch, run tpch queries, iwtpch."
---

# Interactive Analytics TPC-H Benchmark

Guides users through setting up and running the TPC-H benchmark locally against Snowflake Interactive Warehouses.

## Prerequisites

- `uv` installed (Python package runner)
- A Snowflake connection configured in `~/.snowflake/connections.toml`
- Role with privileges to create databases and warehouses (e.g. `SYSADMIN` or `ACCOUNTADMIN`)

## Workflow

### Step 1: Detect Intent

Ask the user what they want to do:

1. **Setup** — Create TPC-H tables + warehouses for benchmarking
2. **Run benchmark** — Execute TPC-H queries against interactive or standard warehouses
3. **Teardown** — Remove benchmark resources

Route to the matching section below.

---

### Setup: Create TPC-H Tables and Warehouses

**Goal:** Copy TPC-H data from `SNOWFLAKE_SAMPLE_DATA` into a local benchmark database with standard and interactive tables.

**Actions:**

1. Ensure `.env` exists in `tpc-h/`:
   ```bash
   cp tpc-h/.env.example tpc-h/.env
   ```

2. Ask the user for configuration:
   - `CONNECTION_NAME` — their Snowflake connection name (from `~/.snowflake/connections.toml`)
   - `SOLUTION_NAME` — prefix for all object names (default: `TPCH`)
   - `DEFAULT_SCALE` — scale factor: 1, 10, 100, or 1000 (default: 10)

3. Update `tpc-h/.env` with the user's values.

4. Run setup:
   ```bash
   cd <REPO_ROOT>/tpc-h && ./iwtpch.sh setup --scale <SCALE>
   ```

**Objects created:**
- Database: `<SOLUTION_NAME>_BENCH_DB`
- Schemas: `TPCH_SF<scale>` (standard tables), `TPCH_SF<scale>_IT` (interactive tables)
- Warehouses: `<SOLUTION_NAME>_BENCH_WH_STD_<scale>` (standard), `<SOLUTION_NAME>_BENCH_WH_INT_<scale>` (interactive)

**Note:** Run setup separately for each scale factor the user wants to benchmark. The interactive warehouse is attached to all 8 interactive tables in the `_IT` schema.

---

### Run Benchmark: Execute TPC-H Queries

**Goal:** Run the 22 TPC-H queries and collect timing results.

**Actions:**

1. Ask the user:
   - Warehouse type: `interactive` (default) or `standard`
   - Tables type: `standard` (default), `interactive`, or `iceberg` (iceberg only if it was set up)
   - Scale: 1, 10, 100, or 1000
   - Workload: `original` (standard SQL) or `modern` (window functions, QUALIFY)
   - Specific queries (optional): comma-separated list like `2,11,15`
   - Repeats per query (default: 3, keeps best time)

2. Run the benchmark:
   ```bash
   cd <REPO_ROOT>/tpc-h && ./iwtpch.sh run \
     --warehouse-type <WAREHOUSE_TYPE> \
     --tables-type <TABLES_TYPE> \
     --scale <SCALE> \
     --workload <WORKLOAD>
   ```

   Optional flags:
   - `--queries 2,11,15` — run only specific queries
   - `--avg` — report the average of the repeats (after one warm-up run) instead of the best
   - `--repeats 5` — best of N executions per query
   - `--iterations 3` — full workload passes

3. Results are saved to `tpc-h/results/run_<warehouse_type>-<tables_type>_sf<scale>_<workload>_<timestamp>.json` and `.csv`.

**Typical comparison flow:**
```bash
./iwtpch.sh run --warehouse-type interactive --tables-type interactive --scale 10 --workload original
./iwtpch.sh run --warehouse-type standard --tables-type standard --scale 10 --workload original
```

At SF1, result validation automatically checks against reference values in `tpc-h-results-1GB.json`.

---

### Teardown

**TPC-H resources** for one scale. By default this drops the warehouses **and** the standard, interactive, and iceberg table schemas for that scale; the database is kept unless `--drop-database` is passed:
```bash
cd <REPO_ROOT>/tpc-h && ./iwtpch.sh teardown --scale <SCALE>
```

To drop less, pass `--tables-type <standard|interactive|iceberg>` and/or `--warehouse-type <standard|interactive>`.

---

## Stopping Points

- After detecting intent — confirm the action before proceeding
- After collecting configuration values — confirm `.env` contents before running setup
- Before teardown — confirm which resources to drop

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `uv: command not found` | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `SNOWFLAKE_SAMPLE_DATA` not accessible | Ensure the role has USAGE on the shared database |
| Connection errors | Verify connection name exists in `~/.snowflake/connections.toml` |

## Output

- TPC-H benchmark results (JSON + CSV) in `tpc-h/results/`
