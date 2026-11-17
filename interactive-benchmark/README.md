# Interactive Benchmark Skill

A [Cortex Code (CoCo)](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code) skill that benchmarks Snowflake **Interactive Warehouse** performance under concurrent load using any user-provided SQL query. The skill automates the entire workflow — from verifying that your tables and queries are suitable for interactive warehouses to deploying the benchmark infrastructure and generating reports — through conversational prompts. Zero-copy is already supported: the skill can benchmark your existing tables directly on an interactive warehouse without duplicating data.

Starting with [Cortex Code (CoCo) CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli) **v1.2.18** and [CoCo Desktop](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop) **v1.22.1**, this skill is available natively — no need to clone this repository. Just open CoCo CLI or CoCo Desktop and invoke `$interactive-benchmark` directly.

See [GETTING-STARTED.md](GETTING-STARTED.md) for a step-by-step walkthrough, the [slide deck](https://snowflake-labs.github.io/analytics/interactive-benchmark/slide-deck/index.html) for an overview presentation, and the [sample report](https://snowflake-labs.github.io/analytics/interactive-benchmark/sample-report/benchmark-report.html) for an example of the generated benchmark output.

## Sample Prompt

Below is a sample prompt you can use with the `interactive-benchmark` CoCo skill. It works against a TPC-H database that can be created using the setup script in the [`benchmarks/tpc-h/`](../benchmarks/tpc-h/) folder (run `./iwtpch.sh setup --scale 100`).

```
$interactive-benchmark hi, I have the following query

SELECT
	N_NAME,
	COUNT(*) AS ORDERS
FROM ORDERS AS O
INNER JOIN CUSTOMER AS C ON O_CUSTKEY = C_CUSTKEY
INNER JOIN NATION   AS N ON C_NATIONKEY = N_NATIONKEY
WHERE O_ORDERDATE BETWEEN '1996-01-01' AND '1996-12-31'
GROUP BY ROLLUP (N_NAME)
ORDER BY N_NAME NULLS LAST;

and I want understand how it can benefit from interactive analytics. The query is
used in Dashboard along with other queries. The query must answer in less than a
second. The database with the table used by the query is DM_TESTTPCH_BENCH_DB and
the schema is TPCH_SF100. The filter on order date will be different and also it
might happen that users filter data for specific nation or region or even market.
How can I make sure that I can obtain the performance I need? 

Please also run a benchmark so that I can see how it performs when there are 50
concurrent users
```

Adapt the database name, schema, scale factor, and connection name to match your own environment.

## Benchmark Reports

When running the skill via CoCo, benchmark reports and artifacts are saved to:

```
~/.snowflake/interactive-benchmark/reports/<SOLUTION_NAME>/
├── benchmark-report.html   # Final HTML report
├── progress.json           # Step-by-step progress tracker
├── locust-run-1.txt        # Raw Locust output for iteration 1
├── locust-run-2.txt        # (if escalation triggered re-runs)
└── ...                     # Additional runs as needed
```
