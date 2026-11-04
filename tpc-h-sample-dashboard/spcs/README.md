# Dashboard + Locust on Snowpark Container Services

This folder contains everything needed to run the TPC-H benchmark dashboard
**and** the Locust load test against it entirely inside Snowflake, with public
ingress URLs you can hit from your laptop.

## Topology

Three services split across two independent compute pools:

```
DASHBOARD_COMPUTE_POOL                LOCUST_COMPUTE_POOL
┌──────────────────────────┐          ┌────────────────────────────────────────┐
│                          │          │                                        │
│  DASHBOARD (dashboard    │          │  DASHBOARD_API_LOCUST (dashboard       │
│  image)                  │          │  image, same /api server)              │
│   - serves UI            │          │                                        │
│   - serves /api/* for    │          │             ▲                          │
│     the browser          │          │             │  http://dashboard-api-   │
│                          │          │             │        locust:3000       │
│                          │          │  DASHBOARD_LOCUST (locust image)       │
│                          │          │   - generates load                     │
└──────────────────────────┘          └────────────────────────────────────────┘
       ▲                                     ▲
       │ user browser (public)               │ locust web UI (public)
```

The API server that services the browser dashboard and the API server that
Locust hits are the **same image running as two separate SPCS services on
different compute pools**. They cannot compete for CPU or memory, so the load
test never becomes a bottleneck for the interactive dashboard experience.
This makes it possible to demonstrate that Interactive Tables sustain much
higher API concurrency than standard tables without the API layer confounding
the results.

```
spcs/
├── config.env           # all knobs (connection, names, resources, locust params)
├── _lib.sh              # shared helpers sourced by every script
├── build-and-push.sh    # docker build + push both images
├── deploy.sh            # two actions: `sql` (run create_lineitem_dashboard.sql) or `services` (full SPCS deploy)
├── list.sh              # list all SPCS resources (registries, repos, services, compute pools)
├── update.sh            # rebuild + ALTER SERVICE (preserves ingress URLs)
├── status.sh            # service state + ingress URLs
├── logs.sh              # tail container logs
├── teardown.sh          # drop services, compute pools, and image repo
├── dashboard/           # dashboard image (Dockerfile, entrypoint, .dockerignore)
├── locust/              # locust image (Dockerfile, entrypoint, .dockerignore)
└── specs/               # SPCS service YAML specs
```

All compute pool / image repository / service lifecycle management goes
through the native `snow spcs compute-pool|image-repository|service`
subcommands rather than raw `snow sql` — see `_lib.sh`. Plain `snow sql` is
only used for the handful of things that CLI surface doesn't cover: creating
the database/schema and resizing the demo warehouses.

## Prerequisites

- `snow` CLI configured with the connection listed in `config.env` (`PM` by default).
  `BUILD_METHOD=spcs` requires `snow` CLI >= 3.16.0 (`snow spcs
  service build-image` doesn't exist before that); 3.18.0+ is recommended —
  the scripts warn if you're below it. `_lib.sh` checks this automatically.
- The connection's role must be able to `CREATE COMPUTE POOL`, `CREATE IMAGE
  REPOSITORY`, and `CREATE SERVICE`. `ACCOUNTADMIN` works.
- The dashboard's runtime role (`DASHBOARD_ROLE`) needs `USAGE` on the TPC-H
  warehouses (`${SOLUTION_NAME}_BENCH_WH_*`) and `SELECT` on the
  `${SOLUTION_NAME}_BENCH_DB.TPCH_SF*_*` schemas.
- Docker Desktop (or any local buildx-capable daemon) — the default build
  method (`BUILD_METHOD=docker`) uses `docker build` / `docker push`.
  If Docker is not available, set `BUILD_METHOD=spcs` in `config.env` to build
  images server-side with `snow spcs service build-image` — see
  [Build methods](#build-methods) below. A helper script (`create-eai.sh`) is
  provided to create the External Access Integration the SPCS build job needs.

## First-time deploy

`deploy.sh` supports two actions:

| Action | What it does |
|--------|------------------------------------------------------|
| `sql` | Runs `sql/create_lineitem_dashboard.sql` with `SOLUTION_NAME` and `SCALE` substituted |
| `services` | Full SPCS deploy (prerequisites + build/push + create services) |

```bash
cd spcs
./deploy.sh sql        # set up the benchmark tables first
./deploy.sh services   # then deploy the SPCS services
```

`deploy.sh services` will:

1. Create `DB.SCHEMA`, **two independent compute pools** (one for the
   dashboard, one for locust), and the image repository (idempotent).
2. Build and push both images to the SPCS image repo.
3. `CREATE SERVICE` for:
   - `DASHBOARD_SERVICE` on `DASHBOARD_COMPUTE_POOL` — the API server that
     answers browser dashboard requests.
   - `LOCUST_API_SERVICE` on `LOCUST_COMPUTE_POOL` — an isolated copy of the
     same API server that Locust hits (never contends with the dashboard).
   - `LOCUST_SERVICE` on `LOCUST_COMPUTE_POOL` — the Locust load generator.
   Or `ALTER SERVICE` if they already exist.
4. Poll `SYSTEM$GET_SERVICE_STATUS` until all three report `READY`.
5. Print the public ingress URLs.

Open the dashboard URL in your browser — Snowflake will prompt for login on
first visit, then the dashboard loads and starts calling `/api/*` on the
dashboard-pool API server.

Open the locust URL to drive load from the Locust web UI. Its target host is
pre-set via SPCS internal DNS to the isolated API server on the locust pool
(`http://dashboard-api-locust:3000`), so the load never hits the same API
process the browser uses.

## Naming convention

All object names are derived from `SOLUTION_NAME` (set in `tpc-h-sample-dashboard/.env`).
With `SOLUTION_NAME=DMTESTTPCH`, the objects created are:

| Object | Name |
|--------|------|
| Database | `DMTESTTPCH_BENCH_DB` |
| Schema | `SPCS` |
| Image repository | `DMTESTTPCH_BENCH_IMAGES` |
| Dashboard compute pool | `DMTESTTPCH_BENCH_DASHBOARD_POOL` |
| Locust compute pool | `DMTESTTPCH_BENCH_LOCUST_POOL` |
| Dashboard service | `DASHBOARD` |
| Locust API service | `DASHBOARD_API_LOCUST` |
| Locust service | `DASHBOARD_LOCUST` |

The general pattern is `${SOLUTION_NAME}_BENCH_*` for infrastructure objects.
Change `SOLUTION_NAME` in `tpc-h-sample-dashboard/.env` to deploy multiple independent
instances in the same account.

## Compute pools

The dashboard and locust services run on **completely independent** compute
pools so they can be sized and scaled without affecting each other. Both pools
are declared in `config.env`:

```
DASHBOARD_COMPUTE_POOL=${SOLUTION_NAME}_BENCH_DASHBOARD_POOL
DASHBOARD_INSTANCE_FAMILY=CPU_X64_M
DASHBOARD_MIN_NODES=1
DASHBOARD_MAX_NODES=2

LOCUST_COMPUTE_POOL=${SOLUTION_NAME}_BENCH_LOCUST_POOL
LOCUST_INSTANCE_FAMILY=CPU_X64_M
LOCUST_MIN_NODES=1
LOCUST_MAX_NODES=2
```

`INSTANCE_FAMILY` is immutable after pool creation. To change it, run
`./teardown.sh` then `./deploy.sh`. Similarly, the compute pool of an existing
service cannot be changed via `ALTER SERVICE` — if you're migrating a deployment
that previously ran both services on the same pool, run `./teardown.sh` before
`./deploy.sh`.

## Iterating

Edit the app code (or `spcs/specs/*.yaml`) and run:

```
./update.sh
```

`update.sh` rebuilds, pushes, and `snow spcs service upgrade`s in place, so
the public ingress URLs stay the same.

Just want to tweak service resources / env without a rebuild?  Edit the yaml
under `specs/` and:

```
./update.sh   # will still rebuild; skip build-and-push manually if you prefer
```

## Auth model inside the container

Every SPCS container gets:

- `SNOWFLAKE_HOST`, `SNOWFLAKE_ACCOUNT` env vars.
- An OAuth token file at `/snowflake/session/token` scoped to the service's
  owner role.

`dashboard/entrypoint.sh` writes a small `~/.snowflake/connections.toml`
pointing at that token file and sets `CONNECTION_NAME=spcs`.  The unchanged
`api/server.py` picks it up via its normal `connections.toml` path.

## Running Locust headlessly

Web UI mode is the default so you can start/stop runs from the browser. For a
one-shot timed run (matches `run-users.sh`), set in `config.env`:

```
LOCUST_HEADLESS=1
LOCUST_RUN_TIME=5m
LOCUST_USERS=5
LOCUST_SPAWN=5
```

Then `./update.sh` (or `./deploy.sh` on a fresh deploy). The container will
exit when the run completes; check results with `./logs.sh locust`.

## Common operations

```
./deploy.sh sql             # run create_lineitem_dashboard.sql
./deploy.sh services        # full SPCS deploy (idempotent)
./list.sh                   # list all SPCS resources in the schema
./status.sh                 # show state + endpoints for all three services
./status.sh --urls-only     # just the ingress URLs
./logs.sh dashboard         # dashboard API (browser-facing) logs
./logs.sh locust-api        # isolated API server that locust hits
./logs.sh locust            # locust load-generator logs
./teardown.sh               # drop services, compute pools, and image repo
```

## Granting another role access to the ingress URLs

By default only the service owner role can hit the public ingress URLs. To let
another role in (substitute your actual `SOLUTION_NAME`):

```sql
USE ROLE ACCOUNTADMIN;
-- With SOLUTION_NAME=DMTESTTPCH the database is DMTESTTPCH_BENCH_DB
GRANT USAGE ON DATABASE <SOLUTION_NAME>_BENCH_DB TO ROLE <consumer_role>;
GRANT USAGE ON SCHEMA <SOLUTION_NAME>_BENCH_DB.SPCS TO ROLE <consumer_role>;
GRANT SERVICE ROLE <SOLUTION_NAME>_BENCH_DB.SPCS.DASHBOARD!ALL_ENDPOINTS_USAGE
  TO ROLE <consumer_role>;
GRANT SERVICE ROLE <SOLUTION_NAME>_BENCH_DB.SPCS.DASHBOARD_API_LOCUST!ALL_ENDPOINTS_USAGE
  TO ROLE <consumer_role>;
GRANT SERVICE ROLE <SOLUTION_NAME>_BENCH_DB.SPCS.DASHBOARD_LOCUST!ALL_ENDPOINTS_USAGE
  TO ROLE <consumer_role>;
```

## Build methods

`BUILD_METHOD` in `config.env` controls how `build-and-push.sh` (and
`update.sh`, `deploy.sh services`) produce the two container images:

- `spcs` — `snow spcs service build-image` uploads the build
  context to a temp stage and runs the build as a job service on
  `BUILD_COMPUTE_POOL`, pushing straight to the image repo. No local Docker
  daemon, no `image-registry login`. This is an experimental `snow` CLI
  command; the scripts enable its feature flag automatically for the
  duration of the run. The build job needs internet egress (apt-get, curl,
  `uv sync` from PyPI) — set `BUILD_EAI_NAME` in `config.env` to one or more
  space-separated external access integrations the build compute pool's role
  can use, or the build will fail trying to reach the network. Run
  `./create-eai.sh` to create the necessary network rule and EAI, then set
  `BUILD_EAI_NAME` in `config.env` to the integration name it prints.
- `docker` (default) — the local `docker build --platform linux/amd64` +
  `docker push` path, useful when you want to build on your own machine (e.g.
  to compare a local build against the SPCS one) or don't have permission to
  run `build-image` jobs.

## Troubleshooting

- `snow spcs image-registry login` errors (only relevant for
  `BUILD_METHOD=docker`): re-run it manually with `--connection $CONNECTION
  --role $ROLE`; tokens expire after ~1h.
- `snow spcs service build-image` errors with "no such command" or doesn't
  show up in `--help`: the experimental feature flag isn't enabled. The
  scripts set `SNOWFLAKE_CLI_FEATURES_ENABLE_SPCS_BUILD_IMAGE=true`
  automatically, but you can also add `[cli.features]
  enable_spcs_build_image = true` to `config.toml` permanently.
- `snow spcs service build-image` build job fails on `apt-get update`, `curl`,
  or `uv sync` with a network/DNS error: the build job's compute pool role
  has no internet egress. Create an external access integration and set
  `BUILD_EAI_NAME` in `config.env` (space-separated if more than one).
- Service stuck in `PENDING`: `./logs.sh dashboard` (or `locust-api` /
  `locust`) — usually a missing grant on the runtime warehouse or a Snowflake
  connection error at startup.
- Dashboard shows connection errors after login: confirm `DASHBOARD_ROLE` has
  access to the TPC-H warehouses and schemas listed above.
- Locust web UI shows "0 requests" or logs `gaierror(-2, 'Name or service not known')`:
  the `LOCUST_HOST` DNS label is wrong. **SPCS converts underscores in the
  service name to hyphens in the DNS name** (Snowflake docs example: service
  `ECHO_SERVICE` → DNS `echo-service.<...>.svc.spcs.internal`). So
  `DASHBOARD_API_LOCUST` must be referenced as `http://dashboard-api-locust:3000`.
  Both services must also live in the same schema (they do by design in
  `config.env`).
