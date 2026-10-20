# Benchmark API + Locust on Snowpark Container Services

The benchmark infrastructure runs the API server
**and** the [Locust](https://locust.io) load test entirely inside Snowflake, with public
ingress URLs you can hit from your laptop.

Container images (Dockerfiles and app source code) live in `interactive-benchmark/spcs-images/`.
The CoCo skill (`.cortex/skills/interactive-benchmark/`) handles deployment and orchestration
using pre-built images.

## Topology

Two services on two independent compute pools:

```mermaid
graph LR
    subgraph API_COMPUTE_POOL["API_COMPUTE_POOL (1–4 nodes, CPU_X64_M)"]
        API1[BENCHMARK_API #1]
        API2[BENCHMARK_API #2]
        API3[BENCHMARK_API #3]
    end

    subgraph LOCUST_COMPUTE_POOL["LOCUST_COMPUTE_POOL (1–2 nodes, CPU_X64_M)"]
        LOCUST[BENCHMARK_LOCUST<br/>× 1 instance]
    end

    LOCUST -- "http://benchmark-api:3000" --> LB
    USER1([curl]) -- "public ingress" --> LB
    LB{{load balancer}} --> API1
    LB --> API2
    LB --> API3
    USER2([locust REST API]) -- "public ingress" --> LOCUST
```

The API service runs **3 instances** (`API_MIN_INSTANCES` / `API_MAX_INSTANCES`)
spread across the API compute pool. Locust runs as a single instance. Instance
counts and pool sizes are configured in `config.env`.

## Directory layout

All paths below are relative to the repo root.

```
.cortex/skills/interactive-benchmark/benchmark/
├── .env                     # solution name + connection (from .env.template)
├── test/                    # benchmark query .sql files
├── reports/                 # output reports (per run)
├── scripts/
│   ├── _lib.sh              # shared helpers (sources spcs/config.env)
│   ├── deploy.sh            # SPCS deploy (prerequisites, create services)
│   ├── upload-queries.sh    # upload .sql files from test/ to the Snowflake stage
│   ├── status.sh            # service state + ingress URLs
│   ├── logs.sh              # tail container logs
│   ├── update.sh            # upload queries + restart API
│   ├── resize-wh.sh         # resize interactive warehouse (size/MCW)
│   ├── list.sh              # list all SPCS resources
│   ├── teardown.sh          # drop services and compute pools
│   └── update-progress.sh   # atomic progress.json updater
└── spcs/
    ├── config.env           # all knobs (connection, names, resources, locust params)
    ├── config.env.template  # template for config.env
    └── specs/               # SPCS service YAML specs

interactive-benchmark/spcs-images/
├── Dockerfile               # one image containing both runtimes
├── role-entrypoint.sh       # BENCHMARK_ROLE=api|locust dispatcher
├── api/                     # benchmark API source
└── locust/                  # Locust source
```

Benchmark query `.sql` files live in `test/` locally and are uploaded to a
Snowflake internal stage (`@BENCHMARK_QUERIES`) during deployment. The API
container mounts this stage at `/app/test/`, so queries can be updated without
rebuilding images.

## Prerequisites

### For deploying and running benchmarks

- `snow` CLI configured with the connection listed in `spcs/config.env` (`PM` by default).
- Python 3 and `envsubst` (`gettext`) for image validation and spec rendering.
- The connection's role must be able to create the benchmark
  database/schema/stage, compute pools, services, and public service endpoints;
  use `DEPLOY_WAREHOUSE`; and read the approved System Registry image.
  `ACCOUNTADMIN` works.
- The API's runtime role (`API_ROLE`) needs `USAGE` on the interactive
  warehouse and `SELECT` on the interactive schema.

## Deploying

```bash
.cortex/skills/interactive-benchmark/benchmark/scripts/deploy.sh
```

`deploy.sh` will:

1. Verify the exact approved image/tag and architecture, then create
   `DB.SCHEMA`, **two independent compute pools** (one for the API, one for
   Locust), and an internal stage for benchmark queries (idempotent).
2. Upload `.sql` files from `benchmark/test/` to the queries stage.
3. `CREATE SERVICE` for:
   - `API_SERVICE` on `API_COMPUTE_POOL` — the benchmark API (with the
     queries stage mounted at `/app/test/`).
   - `LOCUST_SERVICE` on `LOCUST_COMPUTE_POOL` — the Locust load generator.
   Or `ALTER SERVICE` if they already exist.
4. Poll `SYSTEM$GET_SERVICE_STATUS` until both report `READY`.
5. Print the public ingress URLs.

Both services use the immutable approved image
`interactive-analytics/interactive-benchmark:0.3.0` from
`SNOWFLAKE.IMAGES.SNOWFLAKE_IMAGES`; their specs select
`BENCHMARK_ROLE=api` or `BENCHMARK_ROLE=locust`.

## Naming convention

All object names are derived from `SOLUTION_NAME` (set in `benchmark/.env`).
With `SOLUTION_NAME=IW_TPCH`, the objects created are:

| Object | Name |
|--------|------|
| Database | `IW_TPCH_DB` |
| Schema | `SPCS` |

| API compute pool | `IW_TPCH_API_POOL` |
| Locust compute pool | `IW_TPCH_LOCUST_POOL` |
| API service | `BENCHMARK_API` |
| Locust service | `BENCHMARK_LOCUST` |
| Queries stage | `BENCHMARK_QUERIES` |

Change `SOLUTION_NAME` in `benchmark/.env` to deploy multiple independent
instances in the same account.

## Iterating

### Changing queries only (no image rebuild)

Edit or replace `.sql` files in `benchmark/test/`, then:

```bash
.cortex/skills/interactive-benchmark/benchmark/scripts/update.sh
```

This uploads the new queries to the stage and restarts the API service. No
image rebuild is needed — takes seconds instead of minutes.

### Changing application code

Edit the app source in `interactive-benchmark/spcs-images/`, validate both
roles, and publish a new immutable approved-image version through the
maintainer release process. Update `IMAGE_TAG` only after the new tag is
visible in the System Registry; never replace or deploy `latest`.

## Auth model inside the container

Every SPCS container gets:

- `SNOWFLAKE_HOST`, `SNOWFLAKE_ACCOUNT` env vars.
- An OAuth token file at `/snowflake/session/token` scoped to the service's
  owner role.

`spcs/api/entrypoint.sh` (in `interactive-benchmark/spcs-images/api/`) writes a small `~/.snowflake/connections.toml`
pointing at that token file and sets `CONNECTION_NAME=spcs`. The unchanged
`api/server.py` picks it up via its normal `connections.toml` path.

## Running Locust via REST API

The Locust service exposes a public ingress URL. Control it via curl:

```bash
# Start a test
curl -s -X POST <LOCUST_URL>/swarm \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'user_count=10&spawn_rate=5&host=http://benchmark-api:3000'

# Check stats
curl -s <LOCUST_URL>/stats/requests

# Stop the test
curl -s <LOCUST_URL>/stop
```

## Common operations

```bash
SCRIPTS=.cortex/skills/interactive-benchmark/benchmark/scripts

$SCRIPTS/deploy.sh                  # SPCS deploy (idempotent, uses pre-built images)
$SCRIPTS/upload-queries.sh          # upload .sql files to the queries stage
$SCRIPTS/update.sh                  # upload queries + restart API
$SCRIPTS/list.sh                    # list all SPCS resources in the schema
$SCRIPTS/status.sh                  # show state + endpoints for both services
$SCRIPTS/status.sh --urls-only      # just the ingress URLs
$SCRIPTS/logs.sh api                # benchmark API logs
$SCRIPTS/logs.sh locust             # locust load-generator logs
$SCRIPTS/resize-wh.sh --size M      # resize the interactive warehouse
$SCRIPTS/teardown.sh                # drop services and compute pools
```

## Granting another role access to the ingress URLs

By default only the service owner role can hit the public ingress URLs. To let
another role in:

```sql
USE ROLE ACCOUNTADMIN;
GRANT USAGE ON DATABASE <SOLUTION_NAME>_DB TO ROLE <consumer_role>;
GRANT USAGE ON SCHEMA <SOLUTION_NAME>_DB.SPCS TO ROLE <consumer_role>;
GRANT SERVICE ROLE <SOLUTION_NAME>_DB.SPCS.BENCHMARK_API!ALL_ENDPOINTS_USAGE
  TO ROLE <consumer_role>;
GRANT SERVICE ROLE <SOLUTION_NAME>_DB.SPCS.BENCHMARK_LOCUST!ALL_ENDPOINTS_USAGE
  TO ROLE <consumer_role>;
```

## Troubleshooting

- Service stuck in `PENDING`: `$SCRIPTS/logs.sh api` (or `locust`)
  — usually a missing grant on the runtime warehouse.
- Locust shows "0 requests" or logs `gaierror(-2, 'Name or service not known')`:
  the `LOCUST_HOST` DNS label is wrong. **SPCS converts underscores in the
  service name to hyphens in the DNS name** (e.g. `BENCHMARK_API` →
  `benchmark-api`). Both services must live in the same schema.
