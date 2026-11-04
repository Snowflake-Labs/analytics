# Zero-Copy Interactive Dashboard

A sample dashboard that benchmarks Snowflake **Zero-Copy Interactive Warehouse** against a **Standard Warehouse** using TPC-H workloads. Both warehouse types query the same standard tables — the performance difference comes from the warehouse type, not from separate table copies. The dashboard displays live KPIs and charts, while a Locust-based load test simulates concurrent dashboard users to measure query latency under load.

## Repository Structure

```
dashboard/
├── api/          # Python FastAPI backend (Snowflake connector, REST endpoints)
├── public/       # Frontend single-page HTML dashboard (Chart.js)
├── sql/          # SQL scripts to create the denormalized LINEITEM_DASHBOARD table
├── locust/       # Locust load test simulating concurrent dashboard users
└── spcs/         # Snowpark Container Services deployment (Dockerfiles, specs, scripts)
```

### `api/`

FastAPI server that connects to Snowflake and exposes REST endpoints consumed by the dashboard UI. It routes queries to either a Zero-Copy Interactive Warehouse or a Standard Warehouse depending on the selected mode. Both targets query the same schema.

### `public/`

Static HTML/JS frontend with Chart.js visualizations: KPI cards, time-series line charts, doughnut charts, and bar charts. Served directly by the FastAPI backend.

### `sql/`

Contains `create_lineitem_dashboard.sql` which builds the denormalized `LINEITEM_DASHBOARD` table (joining LINEITEM, ORDERS, CUSTOMER, NATION, REGION) in the standard schema.

### `locust/`

Locust workload definition that simulates real dashboard users. Each virtual user fetches configuration, then repeatedly calls all dashboard API endpoints in parallel with randomized segment filters.

### `spcs/`

Everything needed to deploy the dashboard and load test to Snowpark Container Services: Dockerfiles, service specs, and shell scripts for build, deploy, update, status, logs, and teardown.

## Running the Dashboard Locally

1. Copy `.env.example` to `.env` and configure:

   ```
   CONNECTION_NAME=<your_connection>
   SOLUTION_NAME=<your_solution_name>
   DEFAULT_SCALE=100
   ```

   The connection must exist in `~/.snowflake/connections.toml`.

   All Snowflake object names are derived from `SOLUTION_NAME`:

   | Object | Name |
   |---|---|
   | Database | `<SOLUTION_NAME>_BENCH_DB` |
   | Standard warehouse | `<SOLUTION_NAME>_BENCH_WH_STD_<scale>` |
   | Interactive warehouse | `<SOLUTION_NAME>_BENCH_WH_INT_<scale>` |
   | Schema (both targets) | `TPCH_SF<scale>` |

2. Start the server:

   ```bash
   ./run-dashboard.sh
   ```

   This launches the FastAPI backend on port 3000. Open `http://localhost:3000` in your browser.

## Running the Simulation (Load Test)

### Headless mode

```bash
./run-users.sh <warehouse>
```

Where `<warehouse>` is `interactive` or `standard`. This runs Locust against `http://localhost:3000` with 5 concurrent users for 5 minutes.

### Interactive mode (Locust web UI)

```bash
cd locust
uv run locust -f locustfile.py --host http://localhost:3000
```

Open `http://localhost:8089` to configure the number of users, ramp-up rate, and duration.

## Deploying to Snowpark Container Services

See [`spcs/README.md`](spcs/README.md) for full deployment instructions.

The `deploy.sh` script supports two actions:

```bash
cd spcs

# Create the LINEITEM_DASHBOARD table (substitutes SOLUTION_NAME and SCALE from .env)
./deploy.sh sql

# Build images, push to registry, create compute pools and services
./deploy.sh services
```

Run `sql` first to ensure the denormalized table exists, then `services` to deploy the dashboard and load-test containers.
