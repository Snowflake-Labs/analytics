import io
import json
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from snowflake.connector.errors import ProgrammingError

from fakes import FakeSnow
from iwb import config, metrics, runner, warehouse, workload
from iwb.config import ConfigError, Context
from iwb.events import Events

QID1 = "01c793a5-0100-0001-0000-0000000404a9"
QID2 = "01c793a5-0100-0001-0000-0000000404aa"


def base(**overrides):
    raw = {
        "schema_version": 1,
        "context": {"database": "DB", "schema": "S"},
        "workload": {"queries": [{"sql": "select 1", "weight_pct": 60}, {"sql": "select 2", "weight_pct": 40}]},
        "warehouse": {"existing": "IW"},
        "concurrent_users": 10,
        "run_minutes": 1,
    }
    raw.update(overrides)
    return raw


def quiet_events() -> Events:
    return Events("RUN", io.StringIO())


def history_row(qid, text="select 1", qtype="SELECT", status="SUCCESS", db="HDB", schema="HS"):
    return {"QUERY_ID": qid, "QUERY_TEXT": text, "DATABASE_NAME": db, "SCHEMA_NAME": schema,
            "QUERY_TYPE": qtype, "EXECUTION_STATUS": status}


class ConfigTest(unittest.TestCase):
    def test_parses_sql_workload_with_per_query_context(self) -> None:
        raw = base(workload={"queries": [
            {"sql": "select 1", "weight_pct": 100, "context": {"database": "D2", "schema": "S2"}}]})
        cfg = config.parse(raw)
        self.assertEqual(Context("D2", "S2"), cfg.sql_entries[0].context)
        self.assertEqual(60, cfg.run_seconds)

    def test_rejects_semantic_errors(self) -> None:
        cases = {
            "must sum to 100": base(workload={"queries": [{"sql": "select 1", "weight_pct": 50}]}),
            "every query ID or for none": base(workload={"query_ids": [QID1, {"query_id": QID2, "weight_pct": 100}]}),
            "min_cluster_count must not exceed": base(warehouse={"create": {"size": "XSMALL", "min_cluster_count": 3,
                                                                           "max_cluster_count": 2}}),
            "Invalid config": base(concurrent_users=0),
        }
        for message, raw in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ConfigError, message):
                config.parse(raw)

    def test_schema_requires_context(self) -> None:
        raw = base()
        del raw["context"]
        with self.assertRaisesRegex(ConfigError, "'context' is a required property"):
            config.parse(raw)


class WorkloadTest(unittest.TestCase):
    def test_sql_entries_get_ids_and_duplicates_merge(self) -> None:
        cfg = config.parse(base(workload={"queries": [
            {"sql": "select 1", "weight_pct": 30}, {"sql": "select 1 ", "weight_pct": 20},
            {"sql": "select 2", "weight_pct": 50}]}))
        queries = workload.resolve(cfg, FakeSnow())
        self.assertEqual([("q01", 50.0), ("q03", 50.0)], [(q.id, q.weight) for q in queries])

    def test_query_ids_fall_back_to_information_schema(self) -> None:
        snow = (FakeSnow()
                .on(r"ACCOUNT_USAGE", ProgrammingError(msg="no access", errno=2003))
                .on(r"QUERY_HISTORY_BY_USER", [history_row(QID1), history_row(QID2, text="select 2")]))
        cfg = config.parse(base(workload={"query_ids": [QID1, QID2]}))
        queries = workload.resolve(cfg, snow)
        self.assertEqual([50.0, 50.0], [q.weight for q in queries])
        self.assertEqual(Context("HDB", "HS"), queries[0].context)
        self.assertEqual(QID1, queries[0].source)

    def test_query_id_errors(self) -> None:
        cases = {
            "not found": [history_row(QID1)],
            "not a SELECT": [history_row(QID1, qtype="INSERT"), history_row(QID2)],
            "did not succeed": [history_row(QID1, status="FAILED_WITH_ERROR"), history_row(QID2)],
        }
        for message, rows in cases.items():
            snow = FakeSnow().on(r"ACCOUNT_USAGE", rows)
            cfg = config.parse(base(workload={"query_ids": [QID1, QID2]}))
            with self.subTest(message), self.assertRaisesRegex(ConfigError, message):
                workload.resolve(cfg, snow)

    def test_discover_tables_from_plan_and_access_errors(self) -> None:
        plan = {"Operations": [[{"operation": "TableScan", "objects": ["DB.S.T1"]},
                                {"operation": "TableScan", "objects": ["DB.S.T2"]}]]}
        snow = FakeSnow().on(r"EXPLAIN_PLAN_JSON", [{"PLAN": json.dumps(plan)}])
        queries = workload.resolve(config.parse(base()), snow)
        self.assertEqual({"DB.S.T1", "DB.S.T2"}, workload.discover_tables(snow, queries))
        self.assertEqual(2, len(snow.ran(r"^USE SCHEMA DB\.S$")))

        denied = FakeSnow().on(r"EXPLAIN_PLAN_JSON", ProgrammingError(msg="not authorized", errno=2003))
        with self.assertRaisesRegex(ConfigError, "does not compile for this role: .*not authorized"):
            workload.discover_tables(denied, queries)


class WarehouseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep = patch("iwb.warehouse.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_existing_must_be_interactive(self) -> None:
        snow = FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "STANDARD"}])
        with self.assertRaisesRegex(ConfigError, "not INTERACTIVE"):
            warehouse.prepare(config.parse(base()), snow, set(), "RUN", quiet_events())

    def test_create_attaches_tables_and_tags_expiry(self) -> None:
        raw = base(warehouse={"create": {"size": "SMALL", "min_cluster_count": 1, "max_cluster_count": 3}},
                   fallback_warehouse="STD")
        snow = FakeSnow().on(r"SHOW WAREHOUSES LIKE", lambda sql, params: (
            [{"NAME": "IWB_RUN_IW", "TYPE": "INTERACTIVE", "STATE": "STARTED"}] if params == ("IWB_RUN_IW",) else []))
        cfg = config.parse(raw)
        wh = warehouse.prepare(cfg, snow, {"DB.S.T2", "DB.S.T1"}, "RUN", quiet_events())
        self.assertEqual([], snow.ran("^ALTER"))
        warehouse.activate(cfg, snow, wh)
        create = snow.ran(r"CREATE INTERACTIVE WAREHOUSE")[0]
        self.assertIn("IWB_RUN_IW TABLES (DB.S.T1, DB.S.T2)", create)
        self.assertIn("WAREHOUSE_SIZE = 'SMALL' MIN_CLUSTER_COUNT = 1 MAX_CLUSTER_COUNT = 3", create)
        self.assertRegex(create, r"COMMENT = 'iwb:RUN:expires=\d+'")
        self.assertEqual(["ALTER WAREHOUSE IWB_RUN_IW SET FALLBACK_WAREHOUSE = STD"], snow.ran("FALLBACK"))
        self.assertTrue(wh.created)

    def test_existing_is_never_altered_beyond_resume(self) -> None:
        snow = FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "INTERACTIVE", "STATE": "STARTED"}])
        cfg = config.parse(base(fallback_warehouse="STD"))
        wh = warehouse.prepare(cfg, snow, set(), "RUN", quiet_events())
        warehouse.activate(cfg, snow, wh)
        self.assertEqual(["ALTER WAREHOUSE IW RESUME IF SUSPENDED"], snow.ran("^ALTER"))

    def test_cleanup_skips_warehouses_it_cannot_drop(self) -> None:
        now = time.time()
        snow = (FakeSnow()
                .on(r"SHOW WAREHOUSES", [{"NAME": "IWB_A_IW", "COMMENT": f"iwb:A:expires={int(now) - 1}"},
                                          {"NAME": "IWB_B_IW", "COMMENT": f"iwb:B:expires={int(now) - 1}"}])
                .on(r"DROP WAREHOUSE IF EXISTS IWB_A_IW", ProgrammingError(msg="insufficient privileges", errno=3001)))
        warehouse.cleanup_stale(snow, quiet_events(), now)
        self.assertEqual(2, len(snow.ran("^DROP")))
        self.assertEqual(("IWB\\_%",), snow.statements[0][1])

    def test_cleanup_drops_only_expired_runs(self) -> None:
        now = time.time()
        snow = FakeSnow().on(r"SHOW WAREHOUSES", [
            {"NAME": "IWB_OLD_IW", "COMMENT": f"iwb:OLD:expires={int(now) - 10}"},
            {"NAME": "IWB_LIVE_IW", "COMMENT": f"iwb:LIVE:expires={int(now) + 3600}"},
            {"NAME": "IWB_OTHER", "COMMENT": "someone else's"},
        ])
        warehouse.cleanup_stale(snow, quiet_events(), now)
        self.assertEqual(["DROP WAREHOUSE IF EXISTS IWB_OLD_IW"], snow.ran("^DROP"))

    def test_ensure_compiles_attaches_unbound_interactive_table(self) -> None:
        unbound = ProgrammingError(msg="Interactive table IT1 needs to be added to current warehouse. ...",
                                   errno=warehouse.TABLE_NOT_BOUND)
        answers = iter([unbound, [{"PLAN": "{}"}]])

        def explain(sql, params):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        snow = (FakeSnow().on(r"EXPLAIN_PLAN_JSON", explain)
                .on(r"SHOW WAREHOUSES", [{"NAME": "IWB_RUN_IW", "STATE": "STARTED"}]))
        queries = [workload.Query("q01", "select * from IT1", 100, Context("DB", "S"), "sql")]
        wh = warehouse.Warehouse("IWB_RUN_IW", created=True)
        warehouse.ensure_compiles(snow, wh, queries, quiet_events())
        self.assertEqual(["ALTER WAREHOUSE IWB_RUN_IW ADD TABLES (DB.S.IT1)"], snow.ran("ADD TABLES"))

        answers = iter([unbound])
        with self.assertRaisesRegex(ConfigError, "is not attached to IW"):
            warehouse.ensure_compiles(snow, warehouse.Warehouse("IW", created=False), queries, quiet_events())

    def test_drop_only_created(self) -> None:
        snow = FakeSnow()
        warehouse.drop(snow, warehouse.Warehouse("IW", created=False), quiet_events())
        warehouse.drop(snow, warehouse.Warehouse("IWB_RUN_IW", created=True), quiet_events())
        self.assertEqual(["DROP WAREHOUSE IF EXISTS IWB_RUN_IW"], snow.ran("DROP"))


class CliTeardownTest(unittest.TestCase):
    """A created warehouse is dropped whatever fails after CREATE, and result.json is written."""

    def run_cli(self, failing_step: str, error: BaseException) -> tuple[int, FakeSnow, dict]:
        cfg_path = Path(tempfile.mkdtemp()) / "run.json"
        cfg_path.write_text(json.dumps(base(warehouse={"create": {"size": "XSMALL"}})))
        snow = (FakeSnow().on(r"EXPLAIN_PLAN_JSON", [{"PLAN": "{}"}])
                .on(r"SHOW WAREHOUSES LIKE", lambda sql, params: [
                    {"NAME": params[0], "TYPE": "INTERACTIVE", "STATE": "STARTED"}]
                    if params[0].endswith("_IW") else []))
        results = tempfile.mkdtemp()
        with patch("iwb.cli.connect", return_value=snow), \
                patch(f"iwb.{failing_step}", side_effect=error), \
                patch("sys.stdout", io.StringIO()):
            from iwb import cli
            code = cli.run(str(cfg_path), results, "conn")
        result = json.loads(next(Path(results).glob("*/result.json")).read_text())
        return code, snow, result

    def test_activate_failure_drops_created_warehouse(self) -> None:
        code, snow, result = self.run_cli("warehouse.activate", RuntimeError("never STARTED"))
        self.assertEqual(1, code)
        self.assertEqual(1, len(snow.ran(r"^DROP WAREHOUSE IF EXISTS IWB_.*_IW$")))
        self.assertIn("never STARTED", result["error"])

    def test_termination_during_run_still_tears_down(self) -> None:
        code, snow, result = self.run_cli("runner.warm", SystemExit(143))
        self.assertEqual(143, code)
        self.assertEqual(1, len(snow.ran(r"^DROP WAREHOUSE")))
        self.assertEqual(143, result["exit_code"])


class MetricsTest(unittest.TestCase):
    def test_dedupes_retries_and_counts_fallback(self) -> None:
        def row(qid, wh, ms, status="SUCCESS", end=1.0):
            return {"QUERY_ID": qid, "EXECUTION_STATUS": status, "WAREHOUSE_NAME": wh, "CLUSTER_NUMBER": 1,
                    "END_TIME": datetime.fromtimestamp(end, UTC), "TOTAL_ELAPSED_TIME": ms, "COMPILATION_TIME": 1, "EXECUTION_TIME": 1,
                    "QUEUED_MS": 0, "QUERY_TAG": "RUN"}
        snow = FakeSnow().on(r"QUERY_HISTORY_BY_WAREHOUSE", lambda sql, params: {
            "IW": [row("a", "IW", 100), row("b", "IW", 200), row("c", "IW", 5000, "FAILED_WITH_ERROR"),
                   {**row("x", "IW", 1), "QUERY_TAG": "OTHER"}, row("ramp", "IW", 9000, end=0.3)],
            "STD": [row("c", "STD", 7000, end=2)],
        }[params[0]] if params[1] == 0 else [])
        m = metrics.server_side(snow, "DB", "IW", "STD", "RUN", 0.6, 30, quiet_events())
        self.assertEqual((3, 0, 1), (m["n"], m["n_failed"], m["n_fallback"]))
        self.assertEqual(7000, m["p99_ms"])
        self.assertEqual(200, m["p95_interactive_only_ms"])

    def test_full_slices_are_split_and_one_second_overflow_fails(self) -> None:
        limit = metrics.RESULT_LIMIT

        def history(sql, params):
            start, end = params[1], params[2]
            if end - start > 15:
                return [{}] * limit
            return [{"QUERY_ID": f"{start}", "QUERY_TAG": "RUN"}]

        rows = metrics._collect(FakeSnow().on("QUERY_HISTORY", history), "DB", "IW", "RUN", 0, 60)
        self.assertEqual(4, len(rows))

        always_full = FakeSnow().on("QUERY_HISTORY", lambda sql, params: [{}] * limit)
        with self.assertRaisesRegex(RuntimeError, "within one second"):
            metrics._collect(always_full, "DB", "IW", "RUN", 0, 4)

    def test_timed_out_slices_are_split_and_one_second_timeout_raises(self) -> None:
        def history(sql, params):
            start, end = params[1], params[2]
            if end - start > 15:
                raise ProgrammingError(msg="Statement reached its statement or warehouse timeout", errno=630)
            return [{"QUERY_ID": f"{start}", "QUERY_TAG": "RUN"}]

        rows = metrics._collect(FakeSnow().on("QUERY_HISTORY", history), "DB", "IW", "RUN", 0, 60)
        self.assertEqual(4, len(rows))

        timeout = ProgrammingError(msg="timeout", errno=630)
        with self.assertRaises(ProgrammingError):
            metrics._collect(FakeSnow().on("QUERY_HISTORY", timeout), "DB", "IW", "RUN", 0, 4)
        with self.assertRaises(ProgrammingError):
            metrics._collect(FakeSnow().on("QUERY_HISTORY", ProgrammingError(msg="denied", errno=3001)),
                             "DB", "IW", "RUN", 0, 60)

    def test_percentile_nearest_rank(self) -> None:
        self.assertEqual(95, metrics.percentile(list(range(1, 101)), 95))
        self.assertIsNone(metrics.percentile([], 50))


class RunnerParsingTest(unittest.TestCase):
    def test_measured_window_from_benchmark_log(self) -> None:
        log = Path(tempfile.mkdtemp()) / "locust_run.log"
        log.write_text(
            "[2026-10-07 15:46:46,296] host/INFO/locust.runners: Resetting stats\n"
            "noise line\n"
            "[2026-10-07 15:47:46,147] host/INFO/locust.main: --run-time limit reached, stopping test\n"
        )
        start, end = runner.measured_window(log)
        self.assertAlmostEqual(59.851, end - start, places=3)
        log.write_text("no markers\n")
        self.assertIsNone(runner.measured_window(log))

    def test_parse_stats_row_picks_endpoint(self) -> None:
        csv_path = Path(tempfile.mkdtemp()) / "stats.csv"
        header = ("Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
                  "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
                  "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%")
        csv_path.write_text(header + "\nGET,/api/workload,10,0,1,1,1,1,1,1,0,1,1,1,1,1,1,1,1,1,1,1\n"
                            "POST,/api/run/interactive,550,2,99,85.7,10,354,40,9.3,0,99,100,105,110,120,130,150,180,300,350,354\n")
        row = runner.parse_stats_row(csv_path, "/api/run/interactive")
        self.assertEqual((550, 2, 130.0, 180.0), (row["requests"], row["failures"], row["p95_ms"], row["p99_ms"]))


if __name__ == "__main__":
    unittest.main()
