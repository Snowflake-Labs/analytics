"""Tests for launch.py's local logic; the Sandbox SDK itself is stubbed."""

import contextlib
import io
import json
import re
import signal
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SandboxError = type("SandboxError", (Exception,), {})
sys.modules["snowflake.sandbox"] = types.SimpleNamespace(Sandbox=None, SandboxError=SandboxError, StageMount=None)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import launch  # noqa: E402


def event(step, status, **detail):
    return json.dumps({"iwb": "event", "step": step, "status": status, "detail": detail})


class FakeSandbox:
    """Files are read as-is; a `kill -TERM` makes the controller finish its teardown."""

    def __init__(self, files, read_failures=0):
        self.files = dict(files)
        self.read_failures = read_failures
        self.commands = []

    def read_text(self, path):
        if self.read_failures:
            self.read_failures -= 1
            raise SandboxError("transient")
        return self.files[path]

    def write_bytes(self, path, data):
        self.files[path] = data

    def exec(self, cmd):
        self.commands.append(cmd[-1])
        if "kill -TERM" in cmd[-1]:
            self.files[launch.RUN_LOG] += "\n" + event("TEARDOWN", "completed")
            self.files[launch.RUN_RC] = "143\n"
        return types.SimpleNamespace(stdout="")


def finished(rc, exit_code):
    return {
        launch.RUN_RC: rc,
        launch.RUN_LOG: event("RUN", "completed", exit_code=exit_code, result="/r.json"),
        "/r.json": '{"exit_code": 0}',
    }


class LaunchTest(unittest.TestCase):
    def test_image_mode_uploads_only_the_config_and_runs_the_entrypoint(self) -> None:
        sandbox = FakeSandbox({})
        launch.start(sandbox, b'{"run_minutes": 1}', None)
        self.assertEqual({launch.CONFIG: b'{"run_minutes": 1}'}, sandbox.files)
        self.assertIn(f"bash -c 'bash {launch.IMAGE_RUN} {launch.CONFIG}; echo $? > {launch.RUN_RC}'",
                      sandbox.commands[-1])

    def test_bundle_mode_runs_bootstrap_from_the_unpacked_bundle(self) -> None:
        sandbox = FakeSandbox({})
        with mock.patch.object(launch, "zip_bundle", return_value=b"zip"):
            launch.start(sandbox, b"{}", Path("/bundle"))
        self.assertEqual(b"zip", sandbox.files[f"{launch.WORK_DIR}/bundle.zip"])
        self.assertIn(f"cd {launch.CODE_DIR} && bash bootstrap.sh config.json; echo $? > {launch.RUN_RC}",
                      sandbox.commands[-1])

    def test_bundle_has_sources_bootstrap_and_config_without_venvs(self) -> None:
        config = Path(tempfile.mkdtemp()) / "run.json"
        config.write_text('{"run_minutes": 1}')
        bundle = launch.build_bundle(config)
        for path in ("bootstrap.sh", "config.json", "api/server.py", "api/uv.lock",
                     "locust/entrypoint.sh", "locust/uv.lock", "controller/iwb/cli.py", "controller/run.sh",
                     "controller/iwb/iwb-config.schema.json", "controller/uv.lock"):
            self.assertTrue((bundle / path).is_file(), path)
        self.assertFalse(any(p.name == ".venv" for p in bundle.rglob("*")))
        self.assertFalse((bundle / "controller" / "tests").exists())
        names = zipfile.ZipFile(io.BytesIO(launch.zip_bundle(bundle))).namelist()
        self.assertIn("controller/iwb/cli.py", names)

    def test_locks_resolve_from_public_pypi(self) -> None:
        # The sandbox reaches only public indexes; a lock generated behind a mirror fails to install.
        for project in ("api", "locust", "controller"):
            lock = (launch.IMAGE_SOURCES / project / "uv.lock").read_text()
            registries = set(re.findall(r'registry = "([^"]+)"', lock))
            self.assertEqual({"https://pypi.org/simple"}, registries, project)

    def test_relay_prints_only_new_events_and_finds_final(self) -> None:
        logs = "\n".join([
            '{"iwb":"bootstrap","status":"installing"}',
            "pip noise",
            event("VALIDATE", "started"),
            event("RUN", "failed", error="terminated"),
            event("RUN", "completed", exit_code=0, result="/var/tmp/iwb/results/X/result.json"),
        ])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            seen, final = launch.relay_events(logs, 1)
        self.assertEqual(4, seen)
        self.assertEqual(["VALIDATE", "RUN", "RUN"], [json.loads(line)["step"] for line in out.getvalue().splitlines()])
        self.assertEqual("/var/tmp/iwb/results/X/result.json", final["detail"]["result"])

    def test_wait_returns_controller_exit_code_and_prints_result(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = launch.wait(launch._Relay(FakeSandbox(finished("0\n", 0))), deadline=float("inf"))
        self.assertEqual(0, rc)
        self.assertIn('{"exit_code": 0}', out.getvalue())

    def test_wait_reports_bootstrap_code_when_it_differs_from_controller(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = launch.wait(launch._Relay(FakeSandbox(finished("1\n", 0))), deadline=float("inf"))
        self.assertEqual(1, rc)

    def test_wait_tolerates_transient_poll_failures(self) -> None:
        sandbox = FakeSandbox(finished("0\n", 0), read_failures=launch.MAX_POLL_FAILURES - 1)
        with mock.patch.object(launch.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, launch.wait(launch._Relay(sandbox), deadline=float("inf")))

    def test_deadline_stops_controller_before_raising(self) -> None:
        sandbox = FakeSandbox({launch.RUN_RC: "", launch.RUN_LOG: event("LOAD", "started")})
        with mock.patch.object(launch, "start"), mock.patch.object(launch.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(TimeoutError):
                launch.run(sandbox, b"{}", Path("/unused"), deadline=0)
        stop = next(c for c in sandbox.commands if "kill -TERM" in c)
        self.assertLess(stop.index(f"touch {launch.STOP_FILE}"), stop.index(launch.CONTROLLER_PID))
        self.assertEqual("143\n", sandbox.files[launch.RUN_RC])

    def test_stop_retries_sdk_failures_until_teardown_ends(self) -> None:
        sandbox = FakeSandbox({launch.RUN_RC: "", launch.RUN_LOG: event("LOAD", "started")},
                              read_failures=launch.MAX_POLL_FAILURES * 3)
        err = io.StringIO()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))
        with mock.patch.object(launch.time, "sleep"), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            launch.stop(launch._Relay(sandbox))
        self.assertEqual(signal.SIG_IGN, signal.getsignal(signal.SIGINT))
        self.assertEqual(0, sandbox.read_failures)  # retried past every failure, then saw the exit
        self.assertNotIn("did not finish", err.getvalue())
        self.assertEqual(1, sum("kill -TERM" in c for c in sandbox.commands))

    def test_budget_is_whole_minutes_above_the_controller_worst_case(self) -> None:
        config = {"run_minutes": 1.5, "concurrent_users": 10, "workload": {"query_ids": ["a", "b"]}}
        for install in (True, False):
            run_time = launch.run_budget(config, install)
            self.assertEqual(0, run_time.total_seconds() % 60)
            self.assertGreater(run_time.total_seconds(), launch.budget.worst_case_seconds(90, 10, 2))
        self.assertGreater(launch.run_budget(config, True), launch.run_budget(config, False))
        many = {**config, "workload": {"query_ids": ["a"] * 1000}}
        self.assertGreaterEqual((launch.run_budget(many, False) - launch.run_budget(config, False)).total_seconds(),
                                998 * launch.budget.SECONDS_PER_QUERY - 60)

    def test_memory_tier_by_users(self) -> None:
        self.assertEqual(["4g", "16g", "32g"], [launch.memory_tier(u) for u in (50, 200, 500)])


if __name__ == "__main__":
    unittest.main()
