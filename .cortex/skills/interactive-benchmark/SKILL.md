---
name: interactive-benchmark
version: 0.7.1
description: "Benchmark any SQL query on Snowflake Interactive Warehouses. Chains the snowflake-interactive skill first to create interactive tables and optimize queries, then deploys a benchmark API + Locust load test to Snowpark Container Services (SPCS). Use when: benchmarking queries, testing interactive warehouse performance under load, load testing, deploying benchmark infrastructure. Triggers: benchmark, interactive warehouse benchmark, load test, locust, benchmark my query, performance test, stress test, how fast under load, concurrent query performance, can my query handle N users, latency under concurrency, query throughput test."
---

# Interactive Warehouse Benchmark

Benchmarks any user-provided SQL query against a Snowflake Interactive Warehouse under concurrent load, using a FastAPI server deployed to Snowpark Container Services (SPCS) and Locust for load generation. The goal is to determine whether the query can meet a specified latency target (P95) on an interactive warehouse.

**IMPORTANT: This benchmark MUST use the SPCS-deployed API server and Locust load generator. Do NOT create alternative benchmarking approaches (e.g. running queries directly from the client, writing custom scripts, or bypassing Locust). The entire benchmark infrastructure — API server, Locust, compute pools — runs on SPCS.**

## Prerequisites

- `snow` CLI
- Python 3
- `envsubst` (`gettext`)
- A Snowflake connection configured in `~/.snowflake/connections.toml`
- Role with privileges to create the benchmark database/schema/stage,
  warehouses, compute pools, services, and public service endpoints; use the
  deployment warehouse; and read the approved System Registry image. On
  accounts that have `SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE`, granting it to the
  role covers the account privileges (`deploy.sh` checks and says so); the role
  still needs access to the benchmark warehouses and source data
- Ability to use Snowpark Container Services (SPCS)

## Tool Usage

Every step in this skill MUST use the specific tool listed below. Do NOT substitute alternative approaches (e.g. do not use `bash` + `snow sql` instead of `snowflake_sql_execute`).

| Action | Tool | Notes |
|--------|------|-------|
| Run shell commands | `bash` | For `deploy.sh`, `status.sh`, `logs.sh`, `teardown.sh`, `resize-wh.sh`, `update-progress.sh`, `write-report.sh`, `cp`, `update.sh`, `upload-queries.sh`. Scripts live in `benchmark/scripts/`. Use `run_in_background=true` for `deploy.sh`. |
| Monitor background shell | `bash_output` | To check output of background `deploy.sh` (Step 3.6). |
| Read files | `read` | For templates, configs, reference docs, logs. |
| Write / create files | `write` | For `config.env`, `.env`, `benchmark-query.sql`, report HTML, log captures, and initial `progress.json`. |
| Edit existing files | `edit` | For updating specific values in an existing config file without rewriting the whole file. |
| Search file contents | `grep` | For placeholder verification (`{{`) and config sanity checks. |
| Ask user questions | `ask_user_question` | For Phase 1 inputs, Phase 1 confirmation, and cleanup choice (Step 3.13). |
| Open report in browser | `open_browser` | For the final HTML report (Step 3.12). |
| Load sub-skills | `skill` | `snowflake-interactive` (Step 2.1, 3.9), `html-authoring` (Step 3.12). |
| Track progress | `update-progress.sh` | Updates `progress.json` at every step boundary. See Progress Tracking section. |

## Paths

`<SKILL_DIR>` refers to the directory containing this SKILL.md file (`.cortex/skills/interactive-benchmark/`). Shell scripts (deploy, teardown, status, logs, resize, etc.) live in `<SKILL_DIR>/benchmark/scripts/`. SPCS deployment config and service specs live in `<SKILL_DIR>/benchmark/spcs/`. Image source lives separately in `interactive-benchmark/spcs-images/` for maintainers; benchmark runs consume the approved immutable image from the system repository and do not build it.

## SPCS Deployment Topology

The benchmark deploys two services to Snowpark Container Services. After deployment (Step 3.6), inform the user exactly what is running:

| Service | Container Instances | Compute Pool Nodes | Instance Family |
|---------|--------------------:|-------------------:|-----------------|
| **Benchmark API** (FastAPI) | 3 (configurable: `API_MIN_INSTANCES` / `API_MAX_INSTANCES`) | 1-4 (configurable: `API_MIN_NODES` / `API_MAX_NODES`) | CPU_X64_M |
| **Locust** (load generator) | 1 (fixed) | 1-2 (configurable: `LOCUST_MIN_NODES` / `LOCUST_MAX_NODES`) | CPU_X64_M |

**Why 3 API instances?** A single FastAPI/Uvicorn process handles requests sequentially per worker. With 3 instances (each running WORKERS uvicorn workers), the API layer can serve high concurrency without becoming the bottleneck. The baseline test (Step 3.7 Phase 1) validates this.

**Why 1 Locust instance?** Locust is the load *generator*, not the system under test. A single instance can simulate hundreds of concurrent users.

All instance and node counts are configurable in `benchmark/spcs/config.env`.

---

## Workflow

**⚠️ CRITICAL — Keep the user informed ⚠️**
**You MUST give the user clear, human-readable progress updates throughout the entire workflow.** At every step boundary, tell the user what step you are starting and what it does. After completing a step, briefly confirm the outcome before moving on. Bare tool-call blocks with no text are unacceptable — the user should always know what is happening and why.

The workflow has three distinct phases that MUST be followed in order:

1. **Phase 1 — Gather all inputs** from the user (or extract from their request)
2. **Phase 2 — Validate suitability** — confirm the query is a good fit for interactive warehouses. If not, STOP and explain why.
3. **Phase 3 — Run the benchmark** — deploy, load test, analyze, report. This phase runs autonomously within the user-approved limits.

**IMPORTANT — Progress Tracking:** Immediately after the user confirms Phase 1 inputs — before doing ANY Phase 2/3 work — you MUST do BOTH of the following: (a) create the reports folder, and (b) write an initial `progress.json` file. BOTH are NON-NEGOTIABLE; skipping or deferring either of them is a bug. The `progress.json` file is consumed by `update-progress.sh` at every step boundary and by the final HTML report — without it the entire progress pipeline is broken.

**IMPORTANT — Per-Step Updates:** Every step section below starts with a numbered item "1. **PROGRESS UPDATE (mandatory)**". This is the literal first thing you do when entering that step — run `update-progress.sh`. Do not skip it. Do not defer it. Do not treat it as a note. Execute it as an action before any other work in that step.

**Initialization (right after Phase 1 confirmation — do this BEFORE anything else):**

1. Create the reports directory via `bash`: `mkdir -p <SKILL_DIR>/benchmark/reports/<SOLUTION_NAME>/`
2. Use `write` to create `<SKILL_DIR>/benchmark/reports/<SOLUTION_NAME>/progress.json` with ALL 13 steps set to `"pending"`:

```json
{
  "benchmark_name": "<SOLUTION_NAME>",
  "total_steps": 13,
  "current_step": 0,
  "started_at": "<ISO 8601 now>",
  "updated_at": "<ISO 8601 now>",
  "status": "running",
  "steps": [
    { "id": 1,  "name": "Validate query suitability", "status": "pending" },
    { "id": 2,  "name": "Validate interactive setup", "status": "pending" },
    { "id": 3,  "name": "Configure concurrency and fallback", "status": "pending" },
    { "id": 4,  "name": "Save benchmark query", "status": "pending" },
    { "id": 5,  "name": "Configure environment", "status": "pending" },
    { "id": 6,  "name": "Warm the cache", "status": "pending" },
    { "id": 7,  "name": "Deploy to SPCS", "status": "pending" },
    { "id": 8,  "name": "Run baseline test", "status": "pending" },
    { "id": 9,  "name": "Run load test", "status": "pending" },
    { "id": 10, "name": "Collect server-side metrics", "status": "pending" },
    { "id": 11, "name": "Goal check and escalation", "status": "pending" },
    { "id": 12, "name": "Generate HTML report", "status": "pending" },
    { "id": 13, "name": "Teardown or keep services", "status": "pending" }
  ]
}
```

3. Proceed to Phase 2.

**Update protocol — at EVERY step boundary, run `update-progress.sh`:**

The script `<SKILL_DIR>/benchmark/scripts/update-progress.sh` updates `progress.json` atomically. Usage:

```bash
<SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> <step_id> <action>
```

where `<action>` is one of: `start`, `complete`, `fail`, `skip`.

- **Before starting a step:** Run `update-progress.sh <REPORT_DIR> <step_id> start` via `bash`.
- **After completing a step:** Run `update-progress.sh <REPORT_DIR> <step_id> complete` via `bash`.
- **On failure:** Run `update-progress.sh <REPORT_DIR> <step_id> fail`.
- **On completion:** The script auto-sets top-level status to `"completed"` when step 13 is completed.

Only ONE step should be `"in_progress"` at a time. Each step section below begins with a numbered item **"1. PROGRESS UPDATE (mandatory)"** — run `update-progress.sh` as the first action when entering that step.

---

**Snowsight report:** `<SKILL_DIR>/benchmark/scripts/write-report.sh` writes the benchmark to
`SNOWFLAKE.INTERACTIVE_ANALYTICS` (the `BENCHMARK_*` views Snowsight reads), one test per load-test
iteration. Steps 3.4, 3.6, 3.10, 3.12 and 3.13 each call it once, as `created`, `running`,
`iteration`, then `final`. If the role cannot see `WRITE_BENCHMARK_REPORT`, `created` says the report
is skipped and the later calls do nothing; tell the user that granting
`SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE` to the role enables it. Any other failure is an error: show it
to the user.

---

## Phase 1: Gather All Inputs

Collect ALL of the following from the user before proceeding. If the user's initial request already provides some of these values, acknowledge them and only ask for what is missing. Use `ask_user_question` to present all missing items as a single consolidated question — do NOT ask one item at a time. Use `type: "text"` fields with sensible `defaultValue` for each input, so the user only edits what differs from the defaults.

| # | Input | Description | Default |
|---|-------|-------------|---------|
| 1 | **Database name** | Which database contains the tables used by the query? | (required) |
| 2 | **Schema** | Which schema within that database? | (required) |
| 3 | **Interactive warehouse** | Name of an existing interactive warehouse to benchmark, OR let the skill create one dedicated to this benchmark. | skill creates it |
| 4 | **Standard warehouse** | An existing standard warehouse for the suitability check and fallback, OR let the skill create one dedicated to this benchmark. | skill creates it |
| 5 | **Connection name** | Which Snowflake connection (from `~/.snowflake/connections.toml`) to use? | (required) |
| 6 | **P95 latency goal** | Target P95 latency under concurrent load. Any latency figure is interpreted as P95 unless the user explicitly says otherwise. | P95 <= 1 second |
| 7 | **Concurrent users** | How many simulated concurrent users for the load test? | 50 |
| 8 | **Max warehouse size (scale-up limit)** | Maximum SKU the interactive warehouse can grow to (X-Small, Small, Medium, Large, X-Large, ...). Bounds vertical scaling. | Medium |
| 9 | **Max cluster count (scale-out limit)** | Maximum number of clusters. Bounds horizontal scaling. This is a ceiling, not the starting value: Step 3.2 starts from the in-flight query estimate in `references/mcw-sizing.md`, which is usually well below it. | ceil(users/8) * 2 |
| 10 | **Benchmark name** | Short alphanumeric name used as `SOLUTION_NAME` to prefix all created resources. | `IWB_YYYYMMDDHHMM` (e.g. `IWB_202608271430`) |
| 11 | **Max escalation iterations** | Maximum number of scale-up/scale-out iterations before stopping. Bounds the benchmark loop in Step 3.11. | 5 |

**Load** `references/phase1-inputs.md` (via the `read` tool) for warehouse creation options, AUTO_SUSPEND requirements, DDL restrictions, resource transparency rules, and the autonomous execution principle that governs Phase 2 and Phase 3.

**Do not proceed past Phase 1 until ALL items are confirmed.** Present the collected values back to the user in a summary table and use `ask_user_question` with a single confirmation option (e.g. "Confirmed — proceed") to get approval before moving on. The summary table MUST include all 11 inputs plus the queries stage. Example:

| Input                                 | Value                                        |
|---------------------------------------|----------------------------------------------|
| Database                              | DM_TESTTPCH_DB                               |
| Schema                                | TPCH_SF100                                   |
| Interactive warehouse                 | to be created: IWB_202609101430_INT_WH       |
| Standard warehouse                    | to be created: IWB_202609101430_STD_WH       |
| Connection                            | PM                                           |
| P95 latency goal                      | ≤ 1 second                                   |
| Concurrent users                      | 50                                           |
| Max warehouse size (scale-up ceiling) | Large                                        |
| Max cluster count (scale-out ceiling) | 14                                           |
| Benchmark name                        | IWB_202609101430                             |
| Max escalation iterations             | 5                                            |
| Queries stage                         | @IWB_202609101430_DB.SPCS.BENCHMARK_QUERIES  |

Then present the SPCS resources that will be created and the shared images that will be used:

| SPCS Resource                         | Name                                         |
|---------------------------------------|----------------------------------------------|
| SPCS database                         | IWB_202609101430_DB                          |
| SPCS schema                           | IWB_202609101430_DB.SPCS                     |
| API compute pool                      | IWB_202609101430_API_POOL                    |
| Locust compute pool                   | IWB_202609101430_LOCUST_POOL                 |
| API service                           | BENCHMARK_API                                |
| Locust service                        | BENCHMARK_LOCUST                             |
| Queries stage                         | @IWB_202609101430_DB.SPCS.BENCHMARK_QUERIES  |
| Image repository (shared)             | SNOWFLAKE.IMAGES.SNOWFLAKE_IMAGES            |
| Benchmark image                       | interactive-analytics/interactive-benchmark:0.3.0 |
| API image role                        | `BENCHMARK_ROLE=api`                         |
| Locust image role                     | `BENCHMARK_ROLE=locust`                      |

The SPCS resource names are derived from the benchmark name. The image repository and images are shared from the common repository.

The queries stage path is always `@<SOLUTION_NAME>_DB.SPCS.BENCHMARK_QUERIES` — it is derived, not user-supplied.

**MANDATORY — Present the action plan before proceeding.** After the user confirms the summary table, and BEFORE moving to Phase 2, you MUST present a numbered list of all steps, actions, and operations that will be performed throughout the benchmark. This gives the user a clear picture of what will happen. Present it as follows:

> Here is what will happen next:
>
> 1. **Validate query suitability** — Run your query on both a standard and an interactive warehouse to confirm it benefits from interactive execution. If the query is not a good fit, the benchmark stops here.
> 2. **Validate interactive setup** — Confirm the interactive warehouse and tables (or zero-copy configuration) are correctly set up and ready for benchmarking.
> 3. **Configure concurrency and fallback** — Set the multi-cluster count on the interactive warehouse and configure a fallback warehouse for queries that exceed the 5-second timeout.
> 4. **Save the benchmark query** — Write the query to a SQL file that the benchmark API will execute during the load test.
> 5. **Configure environment** — Generate the `.env` and `config.env` files with all connection, warehouse, and Locust settings.
> 6. **Warm the cache** — Run the query several times on the interactive warehouse so the load test measures steady-state performance, not cold-start latency.
> 7. **Deploy to SPCS** — Create compute pools and start the Benchmark API and Locust services on Snowpark Container Services (using pre-built container images). This creates 2 compute pools (CPU_X64_M) that incur credits while running.
> 8. **Run baseline test** — Locust runs a quick test against a no-op endpoint to validate that the infrastructure (API layer, network) is healthy.
> 9. **Run load test** — Locust simulates <CONCURRENT_USERS> concurrent users hitting the interactive warehouse for 3 minutes, collecting latency percentiles and throughput.
> 10. **Collect server-side metrics** — Query Snowflake's `QUERY_HISTORY` to get server-side P50/P95/P99 latencies and cluster usage, isolating Snowflake time from API overhead.
> 11. **Goal check and escalation** — Compare the P95 latency against your goal (<P95_GOAL>). If the goal is not met, automatically scale out (add clusters) or scale up (larger warehouse) within your approved limits and re-run the load test. Up to <MAX_ESCALATION> iterations.
> 12. **Generate HTML report** — Produce a detailed benchmark report with executive summary, latency charts, bottleneck analysis, escalation history, and optimization recommendations.
> 13. **Cleanup** — Present all created resources and let you choose: full cleanup, SPCS-only cleanup, or keep everything for further testing.

Replace `<CONCURRENT_USERS>`, `<P95_GOAL>`, and `<MAX_ESCALATION>` with the actual values from Phase 1. Then use `ask_user_question` with a single confirmation option (e.g. "Confirmed — proceed with the benchmark") to get approval before moving to Phase 2.

---

## Phase 2: Validate Query Suitability

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 1 start`.

**This phase determines whether the query is a good candidate for interactive warehouses. If it is not, STOP HERE — do not proceed to Phase 3.**

**Load** `references/suitability-check.md` (via the `read` tool) for the full Step 2.1 and Step 2.2 procedure.

**Summary:** Invoke `snowflake-interactive` skill to analyze the query and determine the best approach — zero-copy interactive (querying source tables directly) or interactive tables (copied data). The skill owns this decision. Capture `INTERACTIVE_WAREHOUSE`, `INTERACTIVE_SCHEMA`, `OPTIMIZED_QUERY`, and `INTERACTIVE_MODE` (`zero-copy` or `interactive-tables`) from the skill output. Then run the query on both standard and interactive warehouses. **CRITICAL: If the skill chooses interactive-table mode (Path B), explicitly instruct it to use the standard warehouse (`STANDARD_WAREHOUSE` from Phase 1) for ALL DDL and table creation — interactive warehouses reject `CREATE INTERACTIVE TABLE ... AS SELECT` and any CTAS.** If the query exceeds 10s on standard or 5s on interactive, or shows no speedup — **STOP** and do not proceed to Phase 3.

---

## Phase 3: Run the Benchmark

**Only enter this phase after Phase 2 confirms the query is suitable for interactive.**

From this point, everything runs autonomously within the user-approved limits from Phase 1. No further questions are asked unless the limits are exhausted.

### Step 3.1: Validate Interactive Setup

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 1 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 2 start`.

2. Verify the interactive setup is correct before deploying. The validation differs depending on the `INTERACTIVE_MODE` captured in Step 2.1.

**Load** `references/interactive-validation.md` (via the `read` tool) for the full Mode A (zero-copy) and Mode B (interactive tables) validation procedures, including SQL examples, working set sizing guidelines, and failure criteria.

---

### Step 3.2: Configure Concurrency and Fallback

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 2 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 3 start`.

**CRITICAL: Interactive warehouses scale concurrency *horizontally* (multi-cluster), not vertically. Configure `MAX_CLUSTER_COUNT` and a fallback warehouse BEFORE the load test.**

**MANDATORY — Warm-up after any warehouse change:** Every time a warehouse is created, resized, resumed from suspension, or has its cluster count changed (including this initial configuration and every escalation in Step 3.11), you MUST run the cache warm-up procedure (Step 3.5) before measuring performance. Never run a load test against a cold or freshly-reconfigured warehouse.

**Load** `references/mcw-sizing.md` (via the `read` tool) for the MCW sizing formula. Then **Load** `references/concurrency-config.md` for the full configuration procedure: formula application, `resize-wh.sh` usage, Locust resume sequencing, ALTER WAREHOUSE commands, and fallback warehouse setup.

---

### Step 3.3: Save the Benchmark Query

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 3 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 4 start`.

2. The user provides the query to benchmark as part of their request to CoCo. Create `benchmark/test/benchmark-query.sql` from the template file `benchmark/test/benchmark-query.sql.template` by replacing the placeholder content with the actual query:
   1. Use `read` to load `<SKILL_DIR>/benchmark/test/benchmark-query.sql.template`
   2. Replace the placeholder text with the user's query (or the optimized version if the `snowflake-interactive` skill produced one)
   3. Use `write` to save the result to `<SKILL_DIR>/benchmark/test/benchmark-query.sql`

This file is the single query executed against the interactive warehouse during the load test. It will be uploaded to the Snowflake stage automatically during deployment (Step 3.6).

**Updating queries without redeploying:** If you need to change the query after the initial deployment (e.g. during escalation), save the new `.sql` file locally, then run `<SKILL_DIR>/benchmark/scripts/update.sh` to upload the new query and restart the API service.

**If the template file does not exist** or the query is empty after substitution: inform the user of the error, then jump to Step 3.13 (cleanup) — the benchmark cannot proceed without a valid query file.

---

### Step 3.4: Configure Environment

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 4 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 5 start`.

2. Both config files MUST be created from their templates — never edit the templates directly.

1. **Create `benchmark/.env`** from `benchmark/.env.template`:
   Use `read` to load the template, then `write` to create the `.env` file with these exact values:
   ```
   CONNECTION_NAME=<connection from Phase 1>
   SOLUTION_NAME=<benchmark name from Phase 1>
   ```

2. **Create `benchmark/spcs/config.env`** from `benchmark/spcs/config.env.template`:
   Use `read` to load the template, then `write` to create `config.env` with all values populated.

   **Explicitly set** these values in `config.env` from the answers gathered in Phase 1 and the outputs captured in Step 2.1. Do NOT rely on template defaults — the whole run is wrong if any of these drift:

   | Variable | Source | Example |
   |---|---|---|
   | `CONNECTION` | Phase 1 answer | `PM` |
   | `ROLE` | Phase 1 or `SYSADMIN` (use a role with only the privileges listed in Prerequisites; not `ACCOUNTADMIN`) | `SYSADMIN` |
   | `INTERACTIVE_WAREHOUSE` | **Step 2.1 output** — the exact name `snowflake-interactive` created | `DM_TESTTPCH_INT_WH` |
   | `INTERACTIVE_SCHEMA` | **Step 2.1 output** | `TPCH_SF100_INT` |
   | `API_DATABASE` | **Phase 1 answer** | `DM_TESTTPCH_DB` |
   | `LOCUST_USERS` | **Phase 1 answer** — the concurrent-users number | `50` |
   | `LOCUST_RUN_TIME` | Default `3m`, or user-supplied | `3m` |

   After writing, use the `grep` tool on the file to sanity-check that no template placeholder or stale value remains. The `INTERACTIVE_WAREHOUSE` and `LOCUST_USERS` values are the two most common sources of "the benchmark ran with the wrong settings" bugs.

3. If `benchmark/.env` or `benchmark/spcs/config.env` already exist from a previous run, do NOT reuse them blindly. Use `read` to inspect the existing values, then use `edit` to overwrite anything that changed compared to the current Phase 1/Step 2.1 answers.

4. **Snowsight report (mandatory):** once both files are written, run
   `cd <SKILL_DIR>/benchmark/scripts && ./write-report.sh <REPORT_DIR> created --description "<P95 latency goal from Phase 1>"`.
   Add `--warehouse-created` when the skill created the interactive warehouse in Phase 1.

**If any config file creation fails** (template not found, write error, or `grep` finds leftover placeholders after writing): inform the user which config is invalid and why, then jump to Step 3.13 (cleanup) — the benchmark cannot proceed with misconfigured environment files.

**Note on Locust execution model:** As of this skill version, Locust runs in **non-headless mode with `--autostart` inside the container** — no external HTTP calls are needed to trigger the run. There is no `LOCUST_HEADLESS` toggle. See Steps 3.7–3.8 for the execution flow.

---

### Step 3.5: Warm the Cache

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 5 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 6 start`.

2. Before the load test measures anything, warm the interactive warehouse cache so the numbers reflect steady-state performance, not cold-start latency.

**Load** `references/cache-warming.md` (via the `read` tool) for the full cache warming procedure, including SQL examples, iteration guidance, and convergence checks.

---

### Step 3.6: Deploy to SPCS

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 6 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 7 start`.

**IMPORTANT — Cache must be warm before deploy:** Locust auto-starts immediately when its container becomes READY, so the load test will begin as soon as SPCS finishes provisioning. Ensure Step 3.5 (cache warming) is complete before running this step — otherwise Locust measures cold-cache latency.

**IMPORTANT — Image must already exist:** The exact immutable benchmark image configured in `config.env` must be visible in the system image repository before running `deploy.sh`. The deploy preflight verifies the image/tag and its declared architecture before creating compute pools. Image publishing is a maintainer workflow; benchmark users do not need Docker or `CREATE IMAGE REPOSITORY`.

**IMPORTANT — Registry lookup can be slow:** The initial `SHOW IMAGES` preflight
can take up to two minutes even when the image is available. Do not kill or
bypass `deploy.sh` while it remains at `[0/6]`; continue monitoring the
background process.

**IMPORTANT — Runtime access must be proven before provisioning:** `deploy.sh`
requires `API_ROLE` to equal the service-owner `ROLE`, verifies that role can
activate both the interactive and fallback warehouses, and executes every
benchmark query against the configured interactive schema. On failure it stops
before creating compute pools and prints grant remediation SQL.

Use the `bash` tool with `run_in_background=true`:

```bash
cd <SKILL_DIR>/benchmark/scripts && ./deploy.sh
```

This deploys:
- **Benchmark API** — FastAPI server that executes queries against the interactive warehouse
- **Locust** — Load generator that POSTs queries to the API

**Cost note:** This creates 2 compute pools (CPU_X64_M) that incur credits while running. All resources are listed in Step 3.13 where the user chooses to tear down or keep them.

**IMPORTANT — Deployment monitoring:** SPCS deployments can take 3–10 minutes (compute pool provisioning + image pull + container start). **You MUST give the user clear, human-readable progress updates** so the deployment doesn't look stuck. Bare tool-call blocks with no text are unacceptable.

1. Run `deploy.sh` in the background (`run_in_background=true`). Use `bash_output` to check progress.
2. **Every 30 seconds**, poll service status:
   ```bash
   cd <SKILL_DIR>/benchmark/scripts && ./status.sh
   ```
3. **After every poll, write a one-line text update** summarizing the current state. Examples:
   - "Both services still PENDING — compute pools are provisioning. (~1 min elapsed)"
   - "Benchmark API is READY. Locust is PENDING — pulling image. (~3 min elapsed)"
   - "Both services are READY — deployment complete."

   Include: which services are up, what the current status/message is, and roughly how long it has been. This keeps the user informed instead of showing a wall of silent BASH OUTPUT blocks.
4. If a service stays in PENDING for more than 5 minutes, run `./logs.sh` and report any errors to the user. Common causes:
   - Compute pool still provisioning (normal — wait)
   - Image pull in progress (normal — wait)
   - Image not found (verify the configured immutable tag is visible in `SNOWFLAKE.IMAGES.SNOWFLAKE_IMAGES`)
   - Insufficient privileges (check ROLE)
5. If a service enters FAILED state, immediately run `./logs.sh`, show the user the output, and stop.
6. Only proceed to the next step once both services report READY. Then run
   `cd <SKILL_DIR>/benchmark/scripts && ./write-report.sh <REPORT_DIR> running`.
7. **Display the SPCS topology to the user** (see "SPCS Deployment Topology" section above). This makes it clear how many containers are running and how compute is distributed, so the user can judge whether the infrastructure is appropriately sized for their concurrency target.

---

### Steps 3.7–3.8: Baseline Test + Load Test

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 7 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 8 start`.
2. **Mid-step PROGRESS UPDATE (mandatory):** When the baseline completes successfully, run `update-progress.sh <REPORT_DIR> 8 complete && update-progress.sh <REPORT_DIR> 9 start`.

3. **Load** `references/benchmark-execution.md` (via the `read` tool) for the full baseline and load test procedure.

**Summary:** The Locust container runs a two-phase execution model automatically on start: (1) a baseline test against the no-op `/api/run/baseline` endpoint to validate infrastructure, then (2) the real load test against `/api/run/interactive`. No external HTTP calls are needed — auto-start sidesteps SPCS auth. Monitor via `./logs.sh locust`; look for `[baseline] VERDICT: PASS` before the benchmark begins. For subsequent runs (after escalation), restart the Locust service with `ALTER SERVICE ... SUSPEND / RESUME` (`update.sh` only restarts the API). Parse the `/api/run/interactive` row from the Locust CSV for P50, P95, P99 and failure counts, and only use them if the run printed `[benchmark] VERDICT: PASS`.

---

### Step 3.9: Analyze Results and Generate Recommendations

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 9 complete`.

2. After the load test completes, collect **three sets of measurements**:

1. **Baseline (Locust HTTP)** — p99 from the baseline CSV for `/api/run/baseline`. This is the infrastructure overhead floor — the minimum latency added by the API/network layer.
2. **Client-side (Locust HTTP)** — P50, P95, P99 from the Locust CSV for the `/api/run/interactive` endpoint. This is what the end user experiences (HTTP round-trip + API pool + Snowflake).
3. **Server-side (Snowflake)** — P50, P95, P99 computed (with the Step 3.10 queries) from `INFORMATION_SCHEMA.QUERY_HISTORY_BY_WAREHOUSE` over the Locust run window, filtered to the benchmark `QUERY_TAG`, across the interactive and fallback warehouses, including failed and fallback-served queries. This is what Snowflake alone spent (compile + queue + execute).

All three sets of numbers are **mandatory**. The server-side numbers are what proves Snowflake performance; the client-side numbers are what the user's dashboard sees; the baseline numbers establish the infrastructure overhead floor. The **delta between client-side and server-side isolates the API/HTTP overhead from Snowflake's real cost**. If that delta is significantly higher than the baseline p99, there may be connection pool contention or other API-layer issues beyond simple HTTP overhead.

Also collect:
- Throughput (requests/sec) from Locust
- Error rates (Locust) and server-side `N_FAILED` / `N_FALLBACK` (see `references/server-side-validation.md`)

**Latency goal convention:** When the user specifies a latency target (e.g. "queries must complete within 2 seconds"), interpret that as a **P95 target** unless they explicitly state otherwise. Evaluate the goal against **both** client-side and server-side P95 — if server-side meets the goal but client-side does not, the API is the bottleneck; if both fail, the warehouse configuration needs work.

Then invoke the `snowflake-interactive` skill again via `skill(command="snowflake-interactive")` to analyze the benchmark results and produce optimization recommendations:
- Does the query need rewrites or tweaks for better interactive performance?
- Would clustering keys on the interactive tables improve results?
- Are there join or filter patterns that could benefit from search optimization?

Capture these recommendations for the report.

---

### Step 3.10: Post-Benchmark Server-Side Validation

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 10 start`.

2. After collecting the Locust CSV, **you MUST run server-side aggregation queries against Snowflake for the interactive warehouse**. This is not optional — the Locust numbers alone cannot distinguish API/HTTP overhead from Snowflake time.

**Important — use `INFORMATION_SCHEMA.QUERY_HISTORY_BY_WAREHOUSE`, not `ACCOUNT_USAGE.QUERY_HISTORY`.** The `ACCOUNT_USAGE` view has a 45-minute to 3-hour latency and will return zero rows immediately after the benchmark. `INFORMATION_SCHEMA` is fresh within seconds. Run these diagnostic queries via `snowflake_sql_execute` from a **non-interactive** warehouse (e.g. `USE WAREHOUSE COMPUTE_WH`) — running them on the interactive WH will hit the 5-second cancel.

The API sets `QUERY_TAG` to the `SOLUTION_NAME` (benchmark name) on every request, which excludes warm-up, suitability, and preflight queries from the numbers. The tag is the same for every escalation iteration, so each iteration is isolated by its Locust run window. Collect each iteration into its own `QH_RUN_<N>` table and check that its row count matches the Locust request count.

**Load** `references/server-side-validation.md` (via the `read` tool) for the exact SQL queries, delta interpretation rules, and query profile health metrics.

**Snowsight report (mandatory):** after each `QH_RUN_<N>` table is collected, run
`cd <SKILL_DIR>/benchmark/scripts && ./write-report.sh <REPORT_DIR> iteration <N> <RUN_START> <RUN_END>`
with the same epoch-second window. Run it before any escalation changes the warehouse: it records the
warehouse's current size and cluster counts with the iteration's numbers.

**Cluster usage detection:** The aggregate server-side query includes `COUNT(DISTINCT CLUSTER_NUMBER) AS DISTINCT_CLUSTERS_USED` and `MAX(CLUSTER_NUMBER) AS PEAK_CLUSTER_NUMBER`. These tell you exactly how many clusters actually served queries during the load test, even if `MAX_CLUSTER_COUNT` was set higher. Capture both values — they populate the `{{CLUSTERS_ACTUAL}}` placeholder in the report (format: "X of Y" where X is `DISTINCT_CLUSTERS_USED` and Y is the configured `MAX_CLUSTER_COUNT`). Also include the cluster count in each row of the `{{ITERATION_HISTORY}}` table so the reader can see how cluster usage changed across escalation steps.

---

### Step 3.11: Goal Check and Iterative Escalation

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 10 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 11 start`.

2. After collecting the server-side percentiles from Step 3.10, evaluate them against the P95 latency goal captured in Phase 1.

**Load** `references/escalation.md` (via the `read` tool) for the full escalation procedure: Case 1/2/3 decision logic, scale-out vs scale-up selection, `resize-wh.sh` commands, post-resize warm/resume/monitor sequence, limits-reached messaging, and iteration history recording.

---

### Step 3.12: Generate HTML Report

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 11 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 12 start`.

2. **MANDATORY: Load the `html-authoring` skill first** by calling `skill(command="html-authoring")`. This skill provides the sandboxed-HTML rules that must be followed when writing the report file. Load it before any HTML file creation.

**MANDATORY: use the bundled template.** The report MUST be produced by starting from the canonical HTML template shipped with this skill and filling in its `{{PLACEHOLDER}}` tokens. Do NOT hand-author the report from scratch, do NOT change the section order, and do NOT modify the CSS or structure.

**Template path:** `templates/benchmark-report.html.template`

**Output directory:** `<SKILL_DIR>/benchmark/reports/<SOLUTION_NAME>/`

Create a subfolder named after the benchmark (e.g. `reports/IWB_202608271430/`). Save the following files in it using the `write` tool:
- `benchmark-report.html` — the filled-in HTML report (generated once at the end, after all iterations)
- `locust-run-1.txt` — Locust log from the first load test iteration
- `locust-run-2.txt` — Locust log from the second iteration (if escalation triggered a re-run)
- `locust-run-3.txt` — Locust log from the third iteration (if needed)

Each time the load test runs (Step 3.8), capture the full Locust output (via `bash` running `./logs.sh locust`) and save it to the next numbered file using `write`. This ensures every iteration's results are preserved — even runs that did not meet the goal. The final HTML report references the last successful run's data, but earlier runs provide the escalation history.

**Load** `references/report-generation.md` (via the `read` tool) for the full procedure, coverage requirements, and verification steps.

**Report generation procedure:**
1. Use `read` to load the template from `templates/benchmark-report.html.template`
2. Substitute all `{{PLACEHOLDER}}` tokens with collected values
3. Use `write` to save the filled-in report to `<SKILL_DIR>/benchmark/reports/<SOLUTION_NAME>/benchmark-report.html`
4. Use the `grep` tool to verify no `{{` placeholders remain: `grep '{{' <output-file>` must return zero matches
5. Use `open_browser` to open the report for the user
6. **Snowsight report (mandatory):** run
   `cd <SKILL_DIR>/benchmark/scripts && ./write-report.sh <REPORT_DIR> final COMPLETED`

---

### Step 3.13: Resource Summary and Cleanup

1. **PROGRESS UPDATE (mandatory):** Run `bash <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 12 complete && <SKILL_DIR>/benchmark/scripts/update-progress.sh <REPORT_DIR> 13 start`.
2. **End-of-step PROGRESS UPDATE (mandatory):** After cleanup completes, run `update-progress.sh <REPORT_DIR> 13 complete` (this auto-sets top-level status to `"completed"`).
3. **Snowsight report:** if you reached this step because the benchmark could not continue (an earlier step jumped here), first run
   `cd <SKILL_DIR>/benchmark/scripts && ./write-report.sh <REPORT_DIR> final ERROR --message "<why it stopped>"`,
   or `final CANCELLED` if the user stopped it. Skip this when `created` has not run yet.
4. **Load** `references/cleanup.md` (via the `read` tool) for the full cleanup procedure.

**Summary:** Present the user with a table of all created resources (benchmark-created warehouses, schema, tables, SPCS database/schema, compute pools, and services). The shared System Registry is not created or removed by a benchmark run. Use `ask_user_question` with three options: (1) Full cleanup, (2) SPCS only, (3) Keep everything (state that pools and minimum clusters keep billing). For full cleanup, run `./teardown.sh` then drop the benchmark-created schemas/warehouses/database via SQL. Never drop a user-supplied warehouse — restore its original settings instead. For "keep everything", save `SPCS_DEPLOYED=true` to `.env` so future runs skip redeployment.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/run/interactive` | Execute query on interactive warehouse |
| POST | `/api/run` | Alias for `/api/run/interactive` |
| POST | `/api/run/baseline` | No-op endpoint for infrastructure baseline testing |

Request body: `{"query_id": "<id>"}`. Response includes `elapsed_ms`, `row_count`, `warehouse`, `query_id`. The baseline endpoint returns `elapsed_ms: 0` and `null` for warehouse/query_id.

---

## Stopping Points

- ⚠️ **Phase 1** — Do not proceed until all inputs are confirmed by the user
- ⚠️ **Phase 2 (Suitability Check)** — STOP if query exceeds 10s on standard, 5s on interactive, or shows no speedup. Do not enter Phase 3.
- ⚠️ **Step 3.6** — STOP if any SPCS service enters FAILED state. Show logs and do not proceed.
- ⚠️ **Step 3.7** — STOP if baseline test fails (high failure rate or p99). Infrastructure is not healthy.
- ⚠️ **Step 3.8** — If the run prints `[benchmark] VERDICT: FAIL`, do not report or escalate on its numbers. Diagnose the failures, fix, and re-run.
- ⚠️ **Step 3.11** — STOP and ask the user only when both scale-out and scale-up limits are exhausted and the P95 goal is still not met.
- ⚠️ **Step 3.13** — Confirm cleanup choice before dropping any resources.

## Output

- `benchmark/reports/<SOLUTION_NAME>/benchmark-report.html` — HTML report with executive summary, percentile charts, bottleneck diagnosis, escalation path, and optimization recommendations
- `benchmark/reports/<SOLUTION_NAME>/locust-run-N.txt` — Raw Locust logs for each load test iteration (one per escalation step)
- The benchmark in `SNOWFLAKE.INTERACTIVE_ANALYTICS` (`benchmark_id` = `<SOLUTION_NAME>`), for Snowsight, unless the report was skipped
- Snowflake resources (interactive warehouse, tables, SPCS services) — listed in Step 3.13 for cleanup or reuse

## Checklist and Troubleshooting

**Load** `references/checklist-and-troubleshooting.md` for the full "Good Setup" checklist and troubleshooting table. Verify all checklist items pass before considering the benchmark complete.
