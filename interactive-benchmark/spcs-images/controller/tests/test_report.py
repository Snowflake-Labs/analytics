import json
import unittest
from datetime import UTC, datetime

from fakes import FakeSnow
from snowflake.connector.errors import ProgrammingError
from test_controller import QID1, QID2, base, run_cli

from iwb import cli, config, metrics, report, warehouse, workload
from iwb.config import Context


def reporter(resolved=True, **overrides):
    report_cfg = {"warehouse": "STD", "name": "EU", "benchmark_id": "B1", "tags": {"team": "ui"}}
    cfg = config.parse(base(report=report_cfg, **overrides))
    r = report.Reporter(cfg, "RUN", "2026-10-15T00:00:00+00:00")
    if resolved:
        r.queries = [workload.Query("q01", "select 1", 100 / 3, Context("DB", "S"), QID1),
                     workload.Query("q02", "select 2", 200 / 3, Context("DB", "S"), "sql")]
    return r


def calls(snow):
    """The payloads of the procedure calls the fake received."""
    return [json.loads(params[0]) for sql, params in snow.statements if sql.startswith("CALL ")]


def statement(qid, hash_, ms, status="SUCCESS"):
    at = datetime.fromtimestamp(120, UTC)
    return {"QUERY_ID": qid, "QUERY_HASH": hash_, "TOTAL_ELAPSED_TIME": ms, "EXECUTION_STATUS": status,
            "START_TIME": at, "END_TIME": at, "COMPILATION_TIME": 1, "EXECUTION_TIME": ms - 1, "QUEUED_MS": 0,
            "BYTES_SCANNED": 10, "ROWS_PRODUCED": 1, "ERROR_CODE": None, "ERROR_MESSAGE": None}


class WeightsTest(unittest.TestCase):
    def test_rounded_weights_sum_to_exactly_100(self) -> None:
        for weights in ([1, 1, 1], [60, 40], [1] * 7, [100 / 3, 200 / 3]):
            with self.subTest(weights):
                rounded = report.weights_percent(weights)
                self.assertEqual(10000, round(sum(rounded) * 100))
                self.assertTrue(all(round(w, 2) == w for w in rounded))
        self.assertEqual([33.34, 33.33, 33.33], report.weights_percent([1, 1, 1]))


class ReporterTest(unittest.TestCase):
    def test_created_report_has_the_inputs_as_given(self) -> None:
        r = reporter(resolved=False, warehouse={"create": {"size": "SMALL", "max_cluster_count": 2}})
        payload = r.payload("CREATED")
        self.assertEqual(("CREATED", "EU", 10, 60, {"team": "ui"}),
                         (payload["status"], payload["name"], payload["requested_concurrency"],
                          payload["runtime_seconds"], payload["tags"]))
        self.assertEqual([("q01", "select 1", "MANUAL", None, 60), ("q02", "select 2", "MANUAL", None, 40)],
                         [(q["query_id"], q["query_text"], q["source_type"], q["source_reference"],
                           q["weight_percent"]) for q in payload["queries"]])
        test = payload["tests"][0]
        self.assertEqual(("CREATED", "IWB_RUN_IW", "SMALL", 1, 2, True),
                         (test["status"], test["warehouse_name"], test["warehouse_size"], test["min_cluster_count"],
                          test["max_cluster_count"], test["warehouse_created_for_run"]))

    def test_created_report_lists_each_query_id_once_without_text(self) -> None:
        r = reporter(resolved=False, workload={"query_ids": [QID1, QID1, QID2]})
        self.assertEqual([(QID1, "q01", None, "QUERY_HISTORY", QID1, 66.67),
                          (QID2, "q03", None, "QUERY_HISTORY", QID2, 33.33)],
                         [(q["query_id"], q["query_name"], q["query_text"], q["source_type"], q["source_reference"],
                           q["weight_percent"]) for q in r.payload("CREATED")["queries"]])

    def test_running_report_has_the_workload_and_a_running_test(self) -> None:
        payload = reporter().payload("RUNNING")
        self.assertEqual(("B1", "RUNNING", None, 1), (payload["benchmark_id"], payload["status"], payload["result"],
                                                     payload["report_version"]))
        self.assertEqual([(QID1, "QUERY_HISTORY", QID1, 33.33), ("q02", "MANUAL", None, 66.67)],
                         [(q["query_id"], q["source_type"], q["source_reference"], q["weight_percent"])
                          for q in payload["queries"]])
        test = payload["tests"][0]
        self.assertEqual(("RUN_T1", "RUNNING", [], []), (test["test_id"], test["status"], test["metrics"],
                                                         test["executions"]))
        self.assertEqual(({"team": "ui"}, "2026-10-15T00:00:00+00:00"), (payload["tags"], payload["started_at"]))

    def test_measure_keeps_workload_and_per_query_metrics_and_the_slowest_statements(self) -> None:
        rows = [statement(f"s{i}", "H1", 10 + i) for i in range(30)] + [statement("f1", "H2", 900, "FAILED_WITH_ERROR"),
                                                                         statement("x1", "UNKNOWN", 5000)]
        r = reporter(warehouse={"create": {"size": "SMALL"}})
        r.measure(rows, {"H1": "q01", "H2": "q02"}, 60)
        wh = warehouse.planned(r.cfg, "RUN")
        payload = r.payload("COMPLETED", "SUCCESS", wh)
        test = payload["tests"][0]
        self.assertEqual(("IWB_RUN_IW", "SMALL", True), (test["warehouse_name"], test["warehouse_size"],
                                                         test["warehouse_created_for_run"]))
        by_query = {m["query_id"]: m for m in test["metrics"]}
        self.assertEqual({None, QID1, "q02"}, set(by_query))
        self.assertEqual((32, 31, 32 / 60), (by_query[None]["sample_count"], by_query[None]["successful_count"],
                                             by_query[None]["throughput_qps"]))
        self.assertEqual((30, 10, 39), (by_query[QID1]["sample_count"], by_query[QID1]["min_ms"],
                                        by_query[QID1]["max_ms"]))
        executions = test["executions"]
        self.assertEqual(report.MAX_EXECUTIONS, len(executions))
        self.assertEqual(("f1", "q02", "FAILED", 900), (executions[0]["execution_id"], executions[0]["query_id"],
                                                        executions[0]["status"], executions[0]["total_ms"]))
        self.assertNotIn("x1", [e["execution_id"] for e in executions])
        self.assertEqual(["H1", "H2"], [q["query_hash"] for q in payload["queries"]])

    def test_a_create_that_failed_is_not_reported_as_created(self) -> None:
        r = reporter(warehouse={"create": {"size": "SMALL"}})
        wh = warehouse.planned(r.cfg, "RUN")
        wh.created = False
        test = r.payload("ERROR", wh=wh)["tests"][0]
        self.assertEqual((False, False), (test["warehouse_created_for_run"], test["delete_warehouse_after_run"]))

    def test_report_outcome_follows_the_exit_code(self) -> None:
        cases = {(0, "PASS"): ("COMPLETED", "SUCCESS", None),
                 (20, "BENCHMARK_FAIL"): ("COMPLETED", "FAIL", "BENCHMARK_FAIL"),
                 (143, "PASS"): ("CANCELLED", "CANCELLED", "CANCELLED"),
                 (130, None): ("CANCELLED", "CANCELLED", "CANCELLED"),
                 (30, None): ("ERROR", None, "INVALID_CONFIG"),
                 (40, "BASELINE_FAIL"): ("ERROR", None, "BASELINE_FAIL"),
                 (1, "PASS"): ("ERROR", None, "UNEXPECTED_ERROR")}
        for (code, outcome), expected in cases.items():
            with self.subTest(code):
                self.assertEqual(expected, cli._report_outcome(code, outcome))

    def test_query_hashes_skip_hashes_shared_by_two_queries(self) -> None:
        snow = FakeSnow().on(r"QUERY_HISTORY_BY_SESSION", [
            {"QUERY_ID": "w1", "QUERY_HASH": "H1"}, {"QUERY_ID": "w2", "QUERY_HASH": "H2"},
            {"QUERY_ID": "w3", "QUERY_HASH": "H2"}, {"QUERY_ID": "other", "QUERY_HASH": "H3"}])
        self.assertEqual({"H1": "q01"}, report.query_hashes(snow, {"w1": "q01", "w2": "q02", "w3": "q03"}))

    def test_write_calls_the_procedure_with_the_json_on_the_report_warehouse(self) -> None:
        snow = FakeSnow()
        reporter().write(snow, {"benchmark_id": "B1"})
        self.assertEqual("USE WAREHOUSE STD", snow.statements[0][0])
        sql, params = snow.statements[1]
        self.assertEqual(f"CALL {report.PROCEDURE}(PARSE_JSON(%s))", sql)
        self.assertEqual({"benchmark_id": "B1"}, json.loads(params[0]))


class ReportNameTest(unittest.TestCase):
    def _reporter(self, report_dict, **overrides):
        cfg = config.parse(base(report=report_dict, **overrides))
        return report.Reporter(cfg, "RUN", "2026-10-15T00:00:00+00:00")

    def test_report_name_is_stored_not_the_run_prefix(self) -> None:
        r = self._reporter({"warehouse": "STD", "name": "My benchmark"}, name="IWB_abc123")
        self.assertEqual("My benchmark", r.payload("CREATED")["name"])

    def test_schema_rejects_report_without_name(self) -> None:
        with self.assertRaises(config.ConfigError):
            config.parse(base(report={"warehouse": "STD"}))

    def test_schema_accepts_report_name(self) -> None:
        cfg = config.parse(base(report={"warehouse": "STD", "name": "User run name"}))
        self.assertEqual("User run name", cfg.report.name)

    def test_schema_rejects_empty_report_name(self) -> None:
        with self.assertRaises(config.ConfigError):
            config.parse(base(report={"warehouse": "STD", "name": ""}))


class DetailColumnsTest(unittest.TestCase):
    def test_report_columns_are_read_only_for_a_report(self) -> None:
        for detail in (False, True):
            snow = FakeSnow()
            metrics.statements(snow, "DB", "IW", None, "RUN", 0, 1, detail=detail)
            self.assertEqual(detail, all("QUERY_HASH" in sql for sql in snow.ran("QUERY_HISTORY")))


class CliReportTest(unittest.TestCase):
    def run_cli(self, snow):
        code, snow, result, events = run_cli("runner.warm", SystemExit(143), snow, report={"warehouse": "STD", "name": "EU"})
        return code, calls(snow), result, events

    def test_report_written_at_start_and_after_teardown(self) -> None:
        snow = FakeSnow()
        code, calls, result, events = self.run_cli(snow)
        self.assertEqual(143, code)
        self.assertEqual(["CREATED", "RUNNING", "CANCELLED"], [c["status"] for c in calls])
        self.assertEqual(["started", "completed", "started", "completed"],
                         [e["status"] for e in events if e["step"] == "REPORT"])
        statements = [sql for sql, _ in snow.statements]
        self.assertLess(next(i for i, s in enumerate(statements) if s.startswith("DROP")),
                        max(i for i, s in enumerate(statements) if s.startswith("CALL ")))
        self.assertEqual(result["run_id"], result["benchmark_id"])

    def test_report_refused_at_start_fails_before_creating_a_warehouse_and_is_not_retried(self) -> None:
        refused = ProgrammingError(msg="Unknown user-defined function SNOWFLAKE.INTERACTIVE_ANALYTICS."
                                       "WRITE_BENCHMARK_REPORT.", errno=2141)
        snow = FakeSnow().on(r"^CALL ", refused)
        code, _, result, events = self.run_cli(snow)
        self.assertEqual(30, code)
        self.assertEqual([], snow.ran("^CREATE"))
        self.assertEqual(1, len(snow.ran("^CALL ")))
        self.assertIn("Cannot write the benchmark report", result["error"])
        self.assertIn("Grant SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE", result["error"])
        self.assertEqual(["VALIDATE", "RUN"], [e["step"] for e in events if e["status"] == "failed"])

    def test_a_run_that_fails_validation_is_reported_as_error_with_its_inputs(self) -> None:
        code, snow, result, events = run_cli(report={"warehouse": "STD", "name": "EU"}, workload={"query_ids": [QID1]})
        created, final = calls(snow)
        self.assertEqual(30, code)
        self.assertEqual(("CREATED", "ERROR", "INVALID_CONFIG"),
                         (created["status"], final["status"], final["error_code"]))
        self.assertIn("Query IDs not found", final["error_message"])
        self.assertEqual([QID1], [q["query_id"] for q in final["queries"]])
        self.assertEqual([], snow.ran("^CREATE"))
        test = final["tests"][0]
        self.assertEqual((False, False), (test["warehouse_created_for_run"], test["delete_warehouse_after_run"]))
        self.assertTrue(test["warehouse_name"].endswith("_IW"))
        self.assertEqual(["VALIDATE", "RUN"], [e["step"] for e in events if e["status"] == "failed"])

    def test_a_refused_running_write_ends_in_error_before_creating_a_warehouse(self) -> None:
        snow = FakeSnow().on(r"^CALL ", iter([[], ProgrammingError(msg="lock timeout", errno=625), []]))
        code, snow, result, _ = run_cli(snow=snow, report={"warehouse": "STD", "name": "EU"})
        self.assertEqual(30, code)
        self.assertEqual(["CREATED", "RUNNING", "ERROR"], [c["status"] for c in calls(snow)])
        self.assertEqual([], snow.ran("^CREATE"))

    def test_final_report_failure_is_in_the_result(self) -> None:
        snow = FakeSnow().on(r"^CALL ", iter([[], [], ProgrammingError(msg="lock timeout", errno=625)]))
        code, _, result, events = self.run_cli(snow)
        self.assertEqual(143, code)
        self.assertIn("lock timeout", result["report_error"])
        self.assertNotIn("SNOWFLAKE_INTERACTIVE_BENCHMARK_ROLE", result["report_error"])
        self.assertEqual(["completed", "failed"], [e["status"] for e in events
                                                   if e["step"] == "REPORT" and e["status"] != "started"])


if __name__ == "__main__":
    unittest.main()
