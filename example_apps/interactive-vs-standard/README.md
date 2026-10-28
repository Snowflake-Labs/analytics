# The Concurrency Test

Snowflake-hosted Next.js demo comparing a single-cluster XSMALL standard warehouse with a single-cluster XSMALL interactive warehouse. Both run the same four parameterized retail queries against `CONCURRENCY_DEMO_DB.DEMO.SALES`, a real Snowflake table containing 10 million synthetic sales rows.

## Demo Flow

1. Open the hosted app and choose 1 virtual user for a baseline.
2. Click Refresh both to load the retail visuals.
3. Choose 50 or 100 virtual users, then Run comparison. Preparation connects sessions and warms both workloads; preparation is excluded from measured results.
4. While the run is active, change region or month and click Refresh both. The two panels complete independently. A foreground refresh adds one additional query session per warehouse.
5. Compare dashboard refresh p95, query latency, errors, and history-backed server queue time. Download raw query IDs and timings using the run-history download button.
6. Stop active work, then use the power button to suspend the two benchmark warehouses. Do not repeatedly suspend and resume the interactive warehouse between trials.

## Methodology

- Each virtual user has a dedicated preconnected session and at most one outstanding query. There is no client connection pool wait in the measured virtual-user loop.
- A refresh is four sequential queries: totals, daily revenue, category mix, and top products. The visible retail panel reuses one dedicated session per warehouse and measures its whole server request (the first refresh includes connection setup); benchmark refresh percentiles exclude connection setup.
- Query sequences are deterministic by user and cycle. This is a closed-loop workload, so a faster warehouse may execute more cycles. Equal user counts do not imply equal arrival rates or matched total work.
- Both sessions disable result reuse. Warm warehouse data and query caches are retained. No artificial sleeps inside SQL, no warehouse scaling, no fallback warehouse.
- Default concurrency settings are displayed; no automatic concurrency tuning takes place. Warehouse generation and query acceleration may be inherited from account defaults.
- Query-history metrics are eventually available. Pending history is not zero queue time. Percentiles for query and refresh latency use successful completions; failures and incomplete refreshes are shown separately.
- Runs accept only 1, 10, 25, 50 or 100 users per side and 15, 30 or 60 seconds. A hard cap of 4,000 measured queries per warehouse can end a side early. Throughput from a capped run is not a saturation measurement.
- Refreshes cut short by the run deadline, query cap, cancellation, or a query failure appear as incomplete refreshes. They are not necessarily query errors.
- Run history is held in one application process, retains the last 20 runs, and resets on restart/redeploy. Export results before restarting. The deployment uses one instance; do not scale it horizontally without durable coordination.
- Local measurements include network latency from the development machine. Hosted measurements should be repeated for the final presentation. No outcome is guaranteed for every workload.

## Resources and Cost

`sql/setup.sql` records the approved setup. It intentionally uses CREATE without OR REPLACE so rerunning it cannot silently replace existing objects.

- `DEMO_STANDARD_XS`: XSMALL standard, one cluster, 300-second auto-suspend.
- `DEMO_INTERACTIVE_XS`: XSMALL interactive, one cluster, five-second maximum query timeout.
- `CONCURRENCY_DEMO_MONITOR`: shared 10-credit threshold with immediate suspension. Resource monitors are not a guaranteed hard spending cap.
- `CONCURRENCY_DEMO_APP`: scoped deployment/runtime role, with SELECT on synthetic data and usage/monitor/operate on the two dedicated warehouses.
- `SNOWFLAKE_APPS_QUERY_WH`: separate metadata/history warehouse. It is not part of the benchmark or its resource monitor.
- App hosting, build resources and storage are billed separately. Interactive warehouses have a one-hour minimum billing period each time they resume and a 24-hour minimum auto-suspend interval. Explicit suspension is necessary after a demo.

## Development

```sh
npm ci
npm test
npm run build
npm run dev
```

Next.js loads `.env.local` automatically. Create it once to avoid typing env vars on every run:

```sh
# .env.local (gitignored)
SNOWFLAKE_CONNECTION_NAME=PM
SNOWFLAKE_ROLE=CONCURRENCY_DEMO_APP
SNOWFLAKE_WAREHOUSE=SNOWFLAKE_APPS_QUERY_WH
```

Alternatively, pass them inline: `SNOWFLAKE_CONNECTION_NAME=PM SNOWFLAKE_ROLE=CONCURRENCY_DEMO_APP SNOWFLAKE_WAREHOUSE=SNOWFLAKE_APPS_QUERY_WH npm run dev`.

Authentication uses the existing local Snowflake connection or the hosted Snowflake service identity; no credentials belong in source files. The hosted service is intended for trusted demo operators. All users with application access can run the bounded tests and suspend the dedicated warehouses.

## Deployment

```sh
snow app validate --connection PM --role CONCURRENCY_DEMO_APP --secondary-roles NONE
snow app deploy --connection PM --role CONCURRENCY_DEMO_APP --secondary-roles NONE --verbose
snow app open --connection PM --role CONCURRENCY_DEMO_APP --print-only
```

The CLI-generated `app.yml` targets `SNOWFLAKE_APPS.PUBLIC.CONCURRENCY_DEMO`. Deploy as the dedicated owner role; `EXECUTE_AS_ROLE` must not be set for this non-personal database. Service configuration is applied declaratively. The app uses a custom SVG icon.

## Validation Results (2026-09-23)

Measured from the local application against the PM account, not from the hosted container. All measured queries completed without errors. Both warehouses used MAX_CONCURRENCY_LEVEL=8; standard was Gen2.

| Users per warehouse | Think time | Refresh p95 standard | Refresh p95 interactive | Queue p95 standard | Queue p95 interactive |
| --- | --- | --- | --- | --- | --- |
| 50 | 1 second | 1.128 s | 0.411 s | 58 ms | 0 ms |
| 100 | 1 second | 1.779 s | 0.933 s | 279 ms | 44 ms |
| 100 | 3 seconds | 2.029 s | 0.991 s | 318 ms | 49 ms |

The first two runs reached the interactive-side query cap. The third did not. These observations demonstrate lower latency and less queuing, not guaranteed queue-free execution at 100 users. Run IDs: `77b133ee-1460-4ce7-8c7e-af30f3ba46d7`, `d290f2ac-95c0-47bc-aba7-ed83819730e7`, `76ae3d49-02d8-4cb5-8fa8-c8b23bf53fa0`.

The deployed endpoint is https://ggvfaai4s-pm-pm-aws-us-west-2.snowflakecomputing.app. Service ownership was explicitly verified as CONCURRENCY_DEMO_APP; this environment did not honor the CLI role flag during initial creation, so ownership must be verified after any fresh deployment. The initial service was recreated before handoff. Live logs confirm the final server starts. Browser automation was blocked by the environment, so visual and authenticated hosted end-to-end checks remain manual.

Next.js and other affected runtime dependencies were updated within compatible ranges. `npm audit --omit=dev` still reports two high-severity findings associated with the Snowflake SDK's transitive `toml` dependency. The app does not accept TOML uploads; no forced SDK downgrade was applied. Review these before expanding the demo beyond trusted operators.

## Main Files

- `lib/workload.ts`: fixed query templates, filters, workload sequences and input limits.
- `lib/benchmark-client.ts`: per-user sessions, cancellation, query IDs and timing.
- `lib/benchmark.ts`: lifecycle, bounded scheduling, history reconciliation and metrics.
- `app/api/demo/route.ts`: same-origin control API and real dashboard queries.
- `app/page.tsx`: side-by-side warehouse and retail panels.

Application source is typechecked by the production build. Template unit tests execute under Vitest; they are excluded from application TypeScript compilation because the supplied template tests contain incompatible test-only type annotations.
