"""Tests for launch.py's local logic; the Sandbox SDK itself is stubbed."""

import contextlib
import io
import json
import re
import sys
import tempfile
import types
import unittest
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

    def exec(self, cmd):
        self.commands.append(cmd[-1])
        if "kill -TERM" in cmd[-1]:
            self.files[launch.RUN_LOG] += "\n" + event("TEARDOWN", "completed")
            self.files[launch.BOOTSTRAP_RC] = "143\n"
        return types.SimpleNamespace(stdout="")


def finished(rc, exit_code):
    return {
        launch.BOOTSTRAP_RC: rc,
        launch.RUN_LOG: event("RUN", "completed", exit_code=exit_code, result="/r.json"),
        "/r.json": '{"exit_code": 0}',
    }


class LaunchTest(unittest.TestCase):
    def test_bundle_has_sources_bootstrap_and_config_without_venvs(self) -> None:
        config = Path(tempfile.mkdtemp()) / "run.json"
        config.write_text('{"run_minutes": 1}')
        bundle = launch.build_bundle(config)
        for path in ("bootstrap.sh", "config.json", "api/server.py", "api/uv.lock",
                     "locust/entrypoint.sh", "locust/uv.lock", "controller/iwb/cli.py",
                     "controller/iwb/iwb-config.schema.json", "controller/uv.lock"):
            self.assertTrue((bundle / path).is_file(), path)
        self.assertFalse(any(p.name == ".venv" for p in bundle.rglob("*")))
        self.assertFalse((bundle / "controller" / "tests").exists())

    def test_locks_resolve_from_public_pypi(self) -> None:
        # The sandbox reaches only public indexes; a lock generated behind a mirror fails to install.
        for project in ("api", "locust", "controller"):
            lock = (launch.IMAGE_SOURCES / project / "uv.lock").read_text()
            registries = set(re.findall(r'registry = "([^"]+)"', lock))
            self.assertEqual({"https://pypi.org/simple"}, registries, project)

    def test_zip_round_trips_bundle(self) -> None:
        import io, zipfile
        config = Path(tempfile.mkdtemp()) / "run.json"
        config.write_text('{"run_minutes": 1}')
        bundle = launch.build_bundle(config)
        names = zipfile.ZipFile(io.BytesIO(launch.zip_bundle(bundle))).namelist()
        self.assertIn("bootstrap.sh", names)
        self.assertIn("controller/iwb/cli.py", names)

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
        sandbox = FakeSandbox({launch.BOOTSTRAP_RC: "", launch.RUN_LOG: event("LOAD", "started")})
        with mock.patch.object(launch, "start"), mock.patch.object(launch.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(TimeoutError):
                launch.run(sandbox, Path("/unused"), deadline=0)
        self.assertTrue(any("kill -TERM" in c for c in sandbox.commands))
        self.assertEqual("143\n", sandbox.files[launch.BOOTSTRAP_RC])

    def test_budget_is_whole_minutes_above_the_run(self) -> None:
        budget = launch.run_budget({"run_minutes": 1.5, "concurrent_users": 10})
        self.assertEqual(0, budget.total_seconds() % 60)
        self.assertGreater(budget, launch.RUN_MARGIN + launch.timedelta(minutes=1.5))

    def test_memory_tier_by_users(self) -> None:
        self.assertEqual(["4g", "16g", "32g"], [launch.memory_tier(u) for u in (50, 200, 500)])


if __name__ == "__main__":
    unittest.main()
