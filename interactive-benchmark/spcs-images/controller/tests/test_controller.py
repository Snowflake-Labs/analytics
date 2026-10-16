import contextlib
import io
import json
import os
import re
import signal
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fakes import FakeSnow
from snowflake.connector.errors import ProgrammingError

from iwb import budget, config, metrics, runner, warehouse, workload
from iwb.config import ConfigError, Context
from iwb.events import Events
from iwb.snow import session_token_connection

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

    def test_up_to_1000_query_ids(self) -> None:
        ids = [f"01c793a5-0100-0001-0000-{i:012x}" for i in range(1001)]
        self.assertEqual(1000, len(config.parse(base(workload={"query_ids": ids[:1000]})).query_id_entries))
        with self.assertRaises(ConfigError) as raised:
            config.parse(base(workload={"query_ids": ids}))
        self.assertEqual("Invalid config: workload/query_ids: fails maxItems 1000", str(raised.exception))
        with self.assertRaisesRegex(ConfigError, "^Invalid config: workload/query_ids/0: 'x' does not match"):
            config.parse(base(workload={"query_ids": ["x"]}))

    def test_worst_case_grows_with_the_workload(self) -> None:
        small = config.parse(base(workload={"query_ids": [QID1]}))
        large = config.parse(base(workload={"query_ids": [f"01c793a5-0100-0001-0000-{i:012x}" for i in range(1000)]}))
        self.assertEqual(999 * budget.SECONDS_PER_QUERY, large.worst_case_seconds - small.worst_case_seconds)

    def test_schema_requires_context(self) -> None:
        raw = base()
        del raw["context"]
        with self.assertRaisesRegex(ConfigError, "'context' is a required property"):
            config.parse(raw)


def monitored(qid, text="select 1", status="SUCCESS", db="HDB", schema="HS"):
    return {"success": True, "data": {"queries": [
        {"id": qid, "sqlText": text, "databaseName": db, "schemaName": schema, "status": status}]}}


class WorkloadTest(unittest.TestCase):
    def test_sql_entries_get_ids_and_duplicates_merge(self) -> None:
        cfg = config.parse(base(workload={"queries": [
            {"sql": "select 1", "weight_pct": 30}, {"sql": "select 1 ", "weight_pct": 20},
            {"sql": "select 2", "weight_pct": 50}]}))
        queries = workload.resolve(cfg, FakeSnow())
        self.assertEqual([("q01", 50.0), ("q03", 50.0)], [(q.id, q.weight) for q in queries])

    def test_query_ids_resolve_through_monitoring_without_a_warehouse(self) -> None:
        snow = (FakeSnow()
                .on(rf"uuid={QID1}&stmt_type=SELECT", monitored(QID1))
                .on(rf"uuid={QID2}&stmt_type=SELECT", monitored(QID2, text="select 2")))
        cfg = config.parse(base(workload={"query_ids": [QID1, QID2]}))
        queries = workload.resolve(cfg, snow)
        self.assertEqual([50.0, 50.0], [q.weight for q in queries])
        self.assertEqual(Context("HDB", "HS"), queries[0].context)
        self.assertEqual(QID1, queries[0].source)
        self.assertEqual([], snow.ran("WAREHOUSE|ACCOUNT_USAGE"))

    def test_query_id_errors(self) -> None:
        cases = {
            "not found .* last 14 days": FakeSnow().on(rf"uuid={QID1}&stmt_type=SELECT", monitored(QID1)),
            f"{QID2} is not a SELECT$": (FakeSnow().on(rf"uuid={QID1}&stmt_type=SELECT", monitored(QID1))
                                         .on(rf"/monitoring/queries/{QID2}", monitored(QID2))),
            "did not succeed": (FakeSnow().on(rf"uuid={QID1}&stmt_type=SELECT", monitored(QID1, status="FAILED_WITH_ERROR"))
                                .on(rf"uuid={QID2}&stmt_type=SELECT", monitored(QID2))),
        }
        for message, snow in cases.items():
            cfg = config.parse(base(workload={"query_ids": [QID1, QID2]}))
            with self.subTest(message), self.assertRaisesRegex(ConfigError, message):
                workload.resolve(cfg, snow)

    def test_unexpected_monitoring_response_fails(self) -> None:
        snow = FakeSnow().on("/monitoring/", {"success": False, "message": "denied"})
        with self.assertRaisesRegex(RuntimeError, "Unexpected response"):
            workload.resolve(config.parse(base(workload={"query_ids": [QID1]})), snow)

    def test_lookup_warehouse_searches_account_usage_for_older_ids(self) -> None:
        snow = FakeSnow().on(r"ACCOUNT_USAGE", [history_row(QID1)])
        cfg = config.parse(base(workload={"query_ids": [QID1]}, lookup_warehouse="LOOKUP_WH"))
        workload.resolve(cfg, snow)
        self.assertEqual(["USE WAREHOUSE LOOKUP_WH"], snow.ran("^USE WAREHOUSE"))

        denied = FakeSnow().on(r"ACCOUNT_USAGE", ProgrammingError(msg="not authorized", errno=2003))
        with self.assertRaisesRegex(ConfigError, "ACCOUNT_USAGE cannot be read"):
            workload.resolve(cfg, denied)

    def test_lookup_warehouse_requires_query_ids(self) -> None:
        with self.assertRaisesRegex(ConfigError, "only used with workload.query_ids"):
            config.parse(base(lookup_warehouse="LOOKUP_WH"))

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


def prepare(cfg, snow, tables=frozenset()):
    wh = warehouse.planned(cfg, "RUN")
    warehouse.prepare(cfg, snow, set(tables), wh, "RUN", quiet_events())
    return wh


class WarehouseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep = patch("iwb.warehouse.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_existing_must_be_interactive(self) -> None:
        snow = FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "STANDARD"}])
        with self.assertRaisesRegex(ConfigError, "not INTERACTIVE"):
            prepare(config.parse(base()), snow)

    def test_create_attaches_tables_and_tags_expiry(self) -> None:
        raw = base(warehouse={"create": {"size": "SMALL", "min_cluster_count": 1, "max_cluster_count": 3}},
                   fallback_warehouse="STD")
        snow = FakeSnow().on(r"SHOW WAREHOUSES LIKE", lambda sql, params: (
            [{"NAME": "IWB_RUN_IW", "TYPE": "INTERACTIVE", "STATE": "STARTED"}] if params == ("IWB_RUN_IW",) else []))
        cfg = config.parse(raw)
        wh = prepare(cfg, snow, {"DB.S.T2", "DB.S.T1"})
        self.assertEqual([], snow.ran("^ALTER"))
        warehouse.activate(cfg, snow, wh)
        create = snow.ran(r"CREATE INTERACTIVE WAREHOUSE")[0]
        self.assertIn("IWB_RUN_IW TABLES (DB.S.T1, DB.S.T2)", create)
        self.assertIn("WAREHOUSE_SIZE = 'SMALL' MIN_CLUSTER_COUNT = 1 MAX_CLUSTER_COUNT = 3", create)
        expires = int(re.search(r"COMMENT = 'iwb:RUN:expires=(\d+)'", create).group(1))
        self.assertGreater(expires, time.time() + cfg.worst_case_seconds)
        self.assertEqual(["ALTER WAREHOUSE IWB_RUN_IW SET FALLBACK_WAREHOUSE = STD"], snow.ran("FALLBACK"))
        self.assertTrue(wh.created)
        self.assertEqual("STD", wh.fallback)

    def test_started_existing_warehouse_is_never_altered(self) -> None:
        snow = (FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "INTERACTIVE", "STATE": "STARTED"}])
                .on(r"SHOW PARAMETERS LIKE 'FALLBACK_WAREHOUSE' IN WAREHOUSE IW", [{"VALUE": "STD"}]))
        cfg = config.parse(base(fallback_warehouse="std"))
        wh = prepare(cfg, snow)
        warehouse.activate(cfg, snow, wh)
        warehouse.teardown(snow, wh, quiet_events())
        # Only USAGE is needed: resuming would need OPERATE.
        self.assertEqual([], snow.ran("^ALTER"))

    def test_existing_measures_its_own_fallback(self) -> None:
        snow = (FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "INTERACTIVE", "STATE": "STARTED"}])
                .on(r"SHOW PARAMETERS", [{"VALUE": "STD"}]))
        self.assertEqual("STD", prepare(config.parse(base()), snow).fallback)
        for configured in ([{"VALUE": "OTHER"}], [{"VALUE": ""}], []):
            snow = (FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "INTERACTIVE", "STATE": "STARTED"}])
                    .on(r"SHOW PARAMETERS", configured))
            with self.subTest(configured), self.assertRaisesRegex(ConfigError, "only set on created warehouses"):
                prepare(config.parse(base(fallback_warehouse="STD")), snow)

    def test_existing_suspended_warehouse_is_suspended_again(self) -> None:
        states = iter(["SUSPENDED", "SUSPENDED"])
        snow = FakeSnow().on(r"SHOW WAREHOUSES", lambda sql, params: [
            {"NAME": "IW", "TYPE": "INTERACTIVE", "STATE": next(states, "STARTED")}])
        cfg = config.parse(base())
        wh = prepare(cfg, snow)
        warehouse.activate(cfg, snow, wh)
        warehouse.teardown(snow, wh, quiet_events())
        self.assertEqual(["ALTER WAREHOUSE IW RESUME IF SUSPENDED", "ALTER WAREHOUSE IW SUSPEND"], snow.ran("^ALTER"))

    def test_stop_during_resume_still_suspends_again(self) -> None:
        snow = (FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "INTERACTIVE", "STATE": "SUSPENDED"}])
                .on(r"^ALTER WAREHOUSE IW RESUME", SystemExit(143)))
        cfg = config.parse(base())
        wh = prepare(cfg, snow)
        with self.assertRaises(SystemExit):
            warehouse.activate(cfg, snow, wh)
        warehouse.teardown(snow, wh, quiet_events())
        self.assertEqual(["ALTER WAREHOUSE IW SUSPEND"], snow.ran("SUSPEND$"))

    def test_failed_create_is_not_dropped(self) -> None:
        snow = FakeSnow().on(r"^CREATE INTERACTIVE", ProgrammingError(msg="already exists", errno=2002))
        cfg = config.parse(base(warehouse={"create": {"size": "XSMALL"}}))
        wh = warehouse.planned(cfg, "RUN")
        with self.assertRaises(ProgrammingError):
            warehouse.prepare(cfg, snow, set(), wh, "RUN", quiet_events())
        warehouse.teardown(snow, wh, quiet_events())
        self.assertFalse(wh.created)
        self.assertEqual([], snow.ran("^DROP"))

    def test_suspended_existing_warehouse_the_role_cannot_resume(self) -> None:
        snow = (FakeSnow().on(r"SHOW WAREHOUSES", [{"NAME": "IW", "TYPE": "INTERACTIVE", "STATE": "SUSPENDED"}])
                .on(r"^ALTER WAREHOUSE IW RESUME", ProgrammingError(msg="insufficient privileges", errno=3001)))
        cfg = config.parse(base())
        wh = prepare(cfg, snow)
        with self.assertRaisesRegex(ConfigError, "suspended and this role cannot resume it"):
            warehouse.activate(cfg, snow, wh)
        warehouse.teardown(snow, wh, quiet_events())
        self.assertEqual([], snow.ran("SUSPEND$"))

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
        snow = (FakeSnow().on(r"EXPLAIN_PLAN_JSON", iter([unbound, [{"PLAN": "{}"}]]))
                .on(r"SHOW WAREHOUSES", [{"NAME": "IWB_RUN_IW", "STATE": "STARTED"}]))
        queries = [workload.Query("q01", "select * from IT1", 100, Context("DB", "S"), "sql")]
        wh = warehouse.Warehouse("IWB_RUN_IW", created=True)
        warehouse.ensure_compiles(snow, wh, queries, quiet_events())
        self.assertEqual(["ALTER WAREHOUSE IWB_RUN_IW ADD TABLES (DB.S.IT1)"], snow.ran("ADD TABLES"))

        snow = FakeSnow().on(r"EXPLAIN_PLAN_JSON", unbound)
        with self.assertRaisesRegex(ConfigError, "is not attached to IW"):
            warehouse.ensure_compiles(snow, warehouse.Warehouse("IW", created=False), queries, quiet_events())

    def test_drop_only_created(self) -> None:
        snow = FakeSnow()
        warehouse.teardown(snow, warehouse.Warehouse("IW", created=False), quiet_events())
        warehouse.teardown(snow, warehouse.Warehouse("IWB_RUN_IW", created=True), quiet_events())
        self.assertEqual(["DROP WAREHOUSE IF EXISTS IWB_RUN_IW"], snow.ran("DROP"))


def run_cli(failing_step=None, error=None, snow=None, results_root=None, **overrides):
    """cli.run on a config that creates its warehouse, with `failing_step` patched to `error`.

    Returns the exit code, the fake, result.json and the events.
    """
    cfg_path = Path(tempfile.mkdtemp()) / "run.json"
    cfg_path.write_text(json.dumps(base(**{"warehouse": {"create": {"size": "XSMALL"}}, **overrides})))
    snow = ((snow or FakeSnow()).on(r"EXPLAIN_PLAN_JSON", [{"PLAN": "{}"}])
            .on(r"SHOW WAREHOUSES LIKE", lambda sql, params: [
                {"NAME": params[0], "TYPE": "INTERACTIVE", "STATE": "STARTED"}]
                if params[0].endswith("_IW") else []))
    results = results_root or tempfile.mkdtemp()
    out = io.StringIO()
    failing = patch(f"iwb.{failing_step}", side_effect=error) if failing_step else contextlib.nullcontext()
    with patch("iwb.cli.connect", return_value=snow), failing, patch("sys.stdout", out):
        from iwb import cli
        code = cli.run(str(cfg_path), results, "conn")
    result = json.loads(next(Path(results).glob("*/result.json")).read_text())
    return code, snow, result, [json.loads(line) for line in out.getvalue().splitlines()]


class CliTeardownTest(unittest.TestCase):
    """A created warehouse is dropped whatever fails after CREATE, and result.json is written."""

    def run_cli(self, *args, **kwargs) -> tuple[int, FakeSnow, dict]:
        code, snow, result, self.events = run_cli(*args, **kwargs)
        return code, snow, result

    def assert_final_event(self, exit_code: int) -> None:
        final = self.events[-1]
        self.assertEqual(("RUN", exit_code), (final["step"], final["detail"]["exit_code"]))
        self.assertTrue(final["detail"]["result"].endswith("/result.json"))
        self.assertEqual(list(range(1, len(self.events) + 1)), [e["seq"] for e in self.events])

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
        self.assert_final_event(143)

    def test_termination_while_creating_drops_the_warehouse(self) -> None:
        snow = FakeSnow().on(r"^CREATE INTERACTIVE WAREHOUSE", SystemExit(143))
        code, snow, result = self.run_cli(None, None, snow)
        self.assertEqual(143, code)
        name = result["warehouse"]["name"]
        self.assertEqual([f"DROP WAREHOUSE IF EXISTS {name}"], snow.ran(r"^DROP WAREHOUSE"))
        self.assert_final_event(143)

    def test_teardown_failure_is_in_the_result(self) -> None:
        snow = FakeSnow().on(r"^DROP WAREHOUSE IF EXISTS IWB_", ProgrammingError(msg="network", errno=1))
        code, _, result = self.run_cli("runner.warm", SystemExit(143), snow)
        self.assertEqual(143, code)
        self.assertIn("network", result["teardown_error"])
        self.assert_final_event(143)

    def test_second_stop_during_teardown_still_drops_and_writes_the_result(self) -> None:
        from iwb import cli
        self.addCleanup(signal.signal, signal.SIGTERM, signal.signal(signal.SIGTERM, cli._terminate))

        def teardown(snow, wh, events):
            os.kill(os.getpid(), signal.SIGTERM)
            snow.execute(f"DROP WAREHOUSE IF EXISTS {wh.name}")

        with patch("iwb.warehouse.teardown", side_effect=teardown):
            code, snow, _ = self.run_cli("runner.warm", SystemExit(143))
        self.assertEqual(143, code)
        self.assertEqual(1, len(snow.ran(r"^DROP WAREHOUSE")))
        self.assert_final_event(143)

    def test_relative_results_root_is_made_absolute_for_the_api(self) -> None:
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(tempfile.mkdtemp())
        seen = []

        def run_load(cfg, queries, wh, run_id, connection, results_dir, events):
            seen.append(results_dir)
            raise SystemExit(143)

        self.run_cli("runner.run_load", run_load, results_root="relative-results")
        self.assertTrue(seen[0].is_absolute())

    def test_first_stop_ignores_later_ones(self) -> None:
        from iwb import cli
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))
        with self.assertRaises(SystemExit):
            cli._terminate(signal.SIGTERM, None)
        self.assertEqual([signal.SIG_IGN] * 2, [signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)])

    def test_config_error_is_reported_under_its_step(self) -> None:
        snow = FakeSnow().on(r"SHOW PARAMETERS", [{"VALUE": "OTHER"}])
        code, _, _ = self.run_cli(None, None, snow, warehouse={"existing": "IW_IW"}, fallback_warehouse="STD")
        self.assertEqual(30, code)
        failed = [e["step"] for e in self.events if e["status"] == "failed"]
        self.assertEqual(["SETUP_WAREHOUSE", "RUN"], failed)
        self.assert_final_event(30)

    def test_missing_window_fails_instead_of_skipping_server_metrics(self) -> None:
        load = runner.LoadResult(outcome="PASS", baseline=None, client=None, window=None)
        with patch("iwb.runner.run_load", return_value=load):
            code, snow, result = self.run_cli("runner.warm", None)
        self.assertEqual(1, code)
        self.assertIn("no measured window", result["error"])
        self.assertNotIn("server", result)
        self.assertEqual(1, len(snow.ran(r"^DROP WAREHOUSE")))


class SessionTokenConnectionTest(unittest.TestCase):
    def test_renders_from_the_session_token_and_shares_it_through_snowflake_home(self) -> None:
        token = Path(tempfile.mkdtemp()) / "token"
        token.write_text("t")
        env = {"SNOWFLAKE_ACCOUNT": "ACME", "SNOWFLAKE_ACCOUNT_URL": "https://acme_x.example.com/",
               "SNOWFLAKE_TOKEN_FILE_PATH": str(token)}
        with patch.dict(os.environ, env):
            path = session_token_connection("default")
            self.assertEqual(str(path.parent), os.environ["SNOWFLAKE_HOME"])
        self.assertEqual('[default]\naccount = "ACME"\nhost = "acme_x.example.com"\nauthenticator = "oauth"\n'
                         f'token_file_path = "{token}"\n', path.read_text())
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        with patch.dict(os.environ, {**env, "SNOWFLAKE_HOST": "acme.internal"}):
            self.assertIn('host = "acme.internal"', session_token_connection("default").read_text())

    def test_connect_names_the_rendered_file(self) -> None:
        # The connector read SNOWFLAKE_HOME at import, so the rendered file must be passed explicitly.
        token = Path(tempfile.mkdtemp()) / "token"
        token.write_text("t")
        env = {"SNOWFLAKE_ACCOUNT": "ACME", "SNOWFLAKE_ACCOUNT_URL": "acme_x.example.com:443",
               "SNOWFLAKE_TOKEN_FILE_PATH": str(token)}
        with patch.dict(os.environ, env), patch("iwb.snow.snowflake.connector.connect") as connect:
            from iwb.snow import connect as snow_connect
            snow_connect("default")
        path = connect.call_args.kwargs["connections_file_path"]
        self.assertIn('host = "acme_x.example.com"\n', path.read_text())
        self.assertEqual("default", connect.call_args.kwargs["connection_name"])

    def test_no_session_token_uses_the_named_connection(self) -> None:
        with patch.dict(os.environ, {"SNOWFLAKE_ACCOUNT": "ACME", "SNOWFLAKE_ACCOUNT_URL": "acme.example.com",
                                     "SNOWFLAKE_TOKEN_FILE_PATH": "/nonexistent/token"}):
            self.assertIsNone(session_token_connection("default"))


class RunLoadTest(unittest.TestCase):
    def test_api_exit_fails_the_load_without_waiting_for_locust(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "api.py").write_text("raise SystemExit(3)")
        (tmp / "locust.sh").write_text("sleep 60")
        queries = [workload.Query("q01", "select 1", 100, Context("DB", "S"), "sql")]
        env = {"IWB_API_PYTHON": sys.executable, "IWB_API_SERVER": str(tmp / "api.py"),
               "IWB_LOCUST_ENTRYPOINT": str(tmp / "locust.sh")}
        out = io.StringIO()
        started = time.monotonic()
        with patch.dict(os.environ, env), patch("iwb.runner.API_POLL_SECONDS", 0.05):
            load = runner.run_load(config.parse(base()), queries, warehouse.Warehouse("IW", created=False), "RUN",
                                   "conn", tmp, Events("RUN", out))
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(runner.API_EXITED, load.outcome)
        self.assertIn('"step": "LOAD", "status": "failed"', out.getvalue())


class WarmTest(unittest.TestCase):
    def test_statement_timeout_is_reported_not_fatal(self) -> None:
        q = workload.Query("q01", "select slow", 100, Context("DB", "S"), "sql")
        out = io.StringIO()
        snow = FakeSnow().on(r"^select slow", ProgrammingError(msg="timeout", errno=630))
        runner.warm(snow, warehouse.Warehouse("IW", created=True), [q], Events("RUN", out))
        self.assertEqual(runner.WARMUP_ROUNDS, out.getvalue().count('"warning"'))
        with self.assertRaises(ProgrammingError):
            denied = FakeSnow().on(r"^select slow", ProgrammingError(msg="denied", errno=3001))
            runner.warm(denied, warehouse.Warehouse("IW", created=True), [q], Events("RUN", io.StringIO()))


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
        m = metrics.server_side(snow, "DB", "IW", "STD", "RUN", 0.6, 30)
        self.assertEqual((3, 0, 1), (m["n"], m["n_failed"], m["n_fallback"]))
        self.assertEqual(7000, m["p99_ms"])
        self.assertEqual(200, m["p95_interactive_only_ms"])

    def test_slow_success_on_interactive_is_not_fallback(self) -> None:
        row = {"QUERY_ID": "s", "EXECUTION_STATUS": "SUCCESS", "WAREHOUSE_NAME": "IW", "CLUSTER_NUMBER": 1,
               "END_TIME": datetime.fromtimestamp(1, UTC), "TOTAL_ELAPSED_TIME": 5500, "COMPILATION_TIME": 1,
               "EXECUTION_TIME": 1, "QUEUED_MS": 2000, "QUERY_TAG": "RUN"}
        snow = FakeSnow().on(r"QUERY_HISTORY_BY_WAREHOUSE", lambda sql, params: [row] if params[1] == 0 else [])
        m = metrics.server_side(snow, "DB", "IW", None, "RUN", 0, 30)
        self.assertEqual((1, 0), (m["n"], m["n_fallback"]))

    def test_full_slices_are_split_and_one_second_overflow_fails(self) -> None:
        limit = metrics.RESULT_LIMIT

        def history(sql, params):
            start, end = params[1], params[2]
            if end - start > 15:
                return [{}] * limit
            return [{"QUERY_ID": f"{start}", "QUERY_TAG": "RUN"}]

        snow = FakeSnow().on("QUERY_HISTORY", history)
        rows = metrics._collect(snow, "DB", "IW", "RUN", 0, 120)
        self.assertEqual(8, len(rows))
        # 60 s and 30 s come back full; the remaining window is read in 15 s slices directly.
        self.assertEqual(10, len(snow.ran("QUERY_HISTORY")))

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
        with self.assertRaisesRegex(RuntimeError, "statement timeout"):
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
