# Benchmarks

Benchmark harnesses for Snowflake **standard** and **interactive** warehouses, over **standard**, **interactive** and **iceberg** tables, at scale factors 1, 10, 100 and 1000.

| Benchmark | Folder | CLI | Queries |
|---|---|---|---|
| TPC-H | [`tpc-h/`](tpc-h/) | `./iwtpch.sh` (`iw-tpch`) | 22 (original, modern) |
| SSB (Star Schema Benchmark) | [`ssb/`](ssb/) | `./iwssb.sh` (`iw-ssb`) | 13 (original, modern, flat) |

See each folder's README for setup, usage and benchmark-specific details.

## Layout

```
benchmarks/
├── pyproject.toml     # uv workspace (members: core, tpc-h, ssb) + ruff config
├── uv.lock            # one lockfile for the workspace
├── core/              # bench-core: the shared harness
│   └── src/bench_core/
│       ├── spec.py            # BenchmarkSpec: everything that differs between benchmarks
│       ├── config.py          # configure(spec), naming (DB/schemas/warehouses), SQL tokens
│       ├── cli.py             # argparse (setup / list / run / teardown) + main(spec)
│       ├── commands.py        # command implementations
│       ├── connection.py      # Snowflake connect + session
│       ├── execution.py       # best-of / average query execution
│       ├── queries.py         # load query files, parse --query / --queries via the spec
│       ├── results.py         # stats, printing, JSON/CSV output
│       ├── snowflake_results.py # save run results to the hybrid table
│       ├── sql_scripts.py     # DDL script runner
│       ├── validation.py      # SF1 result validation
│       └── sql/               # shared SQL templates: warehouses, results table, teardown
├── tpc-h/             # TPC-H spec, queries, table setup SQL, reference results
└── ssb/               # SSB spec, queries, source views + table setup SQL, reference results
```

Each benchmark folder keeps its own `.env`, `results/`, wrapper script and version. `uv` creates one virtualenv for the whole workspace in `benchmarks/.venv`. The wrappers (`./iwtpch.sh`, `./iwssb.sh`) work from their own folder as before.

```bash
cd benchmarks/tpc-h   # or benchmarks/ssb
uv sync
cp .env.example .env  # then edit CONNECTION_NAME, SOLUTION_NAME, ...
./iwtpch.sh --help
```

### SQL scripts

Scripts are looked up in the benchmark's `sql/` folder first, then in `core/src/bench_core/sql/`. The shared templates use these tokens in addition to `{{SOLUTION_NAME}}`, `{{SCALE}}`, etc.:

- `{{SCHEMA_PREFIX}}` — the spec's `schema_prefix`
- `{{RESULTS_SCHEMA}}` — the spec's `results_schema`
- `{{TABLE_LIST}}` — the spec's `tables`, fully qualified, for the interactive warehouse

Each benchmark only has to provide `setup_standard_tables.sql`, `setup_interactive_tables.sql` and `setup_iceberg_tables.sql`. Optional extra scripts run before table setup or after a full teardown (`pre_setup_scripts` / `post_teardown_scripts`).

## Adding a benchmark

1. Create `benchmarks/<name>/` with `queries/<workload>/query_*.sql`, the three table setup scripts in `sql/`, and (optionally) an SF1 reference JSON.
2. Add `src/<pkg>/cli.py` defining a `BenchmarkSpec` (name, schema prefix, results schema, tables, query ids, file-stem → id mapping) and a `main()` that calls `bench_core.cli.main(SPEC, argv, workloads=...)`. Use [`tpc-h/src/tpch/cli.py`](tpc-h/src/tpch/cli.py) or [`ssb/src/ssb/cli.py`](ssb/src/ssb/cli.py) as a template.
3. Add a `pyproject.toml` depending on `bench-core` (`[tool.uv.sources] bench-core = { workspace = true }`), plus a wrapper script.
4. Add the folder to `members` in [`pyproject.toml`](pyproject.toml) and run `uv lock`.
