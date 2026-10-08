"""Run one interactive benchmark in a Cortex Sandbox and print its events.

Reference client for the sandbox host (the Snowsight integration does the same calls):
create a sandbox as the given role, upload the API, Locust and controller sources with
bootstrap.sh and the config, start `bash bootstrap.sh config.json` detached, relay its
`{"iwb": "event", ...}` lines until the final RUN event, then print result.json.

The SDK's `code=`/`command=` path is not used: in snowflake-sandbox-python 0.2.2a4 it drops
`role` and `idle_suspend`, runs the command before the bundle is extracted, and keeps the
sandbox up after the command exits, so `poll()` never reports completion.

Requires: pip install "snowflake-sandbox-python==0.2.2a4"   (Cortex Sandboxes, private preview)
See README.md in this directory.

Usage:
  python launch.py --config run.json --connection my-conn [--role ROLE] [--results-stage DB.SCHEMA.STAGE]
"""

from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import signal
import sys
import tempfile
import time
import zipfile
from datetime import timedelta
from pathlib import Path

from snowflake.sandbox import Sandbox, SandboxError, StageMount

HERE = Path(__file__).resolve().parent
IMAGE_SOURCES = HERE.parent / "spcs-images"
WORK_DIR = "/var/tmp/iwb"
CODE_DIR = f"{WORK_DIR}/code"
RUN_LOG = f"{WORK_DIR}/run.log"
BOOTSTRAP_RC = f"{WORK_DIR}/bootstrap.rc"
CONTROLLER_PID = f"{WORK_DIR}/controller.pid"
RESULTS_MOUNT = "/iwb/results"
POLL_SECONDS = 15
MAX_POLL_FAILURES = 5
# Above the controller's own worst case (warehouse start 15 min, Locust slack 21 min plus
# 2 s per user, setup and teardown), so the controller times out first and cleans up.
RUN_MARGIN = timedelta(minutes=60)
# SIGTERM to the controller runs its teardown: stop Locust and the API, drop a created warehouse.
STOP_GRACE = timedelta(minutes=5)


def memory_tier(users: int) -> str:
    """API and Locust share one container; tiers set CPU (4g=2, 16g=4, 32g=6 cores)."""
    if users <= 50:
        return "4g"
    if users <= 200:
        return "16g"
    return "32g"


def build_bundle(config_path: Path) -> Path:
    bundle = Path(tempfile.mkdtemp(prefix="iwb-bundle-"))
    ignore = shutil.ignore_patterns(".venv", "__pycache__", "tests", "*.pyc")
    for project in ("api", "locust", "controller"):
        shutil.copytree(IMAGE_SOURCES / project, bundle / project, ignore=ignore)
    shutil.copy2(HERE / "bootstrap.sh", bundle / "bootstrap.sh")
    shutil.copy2(config_path, bundle / "config.json")
    return bundle


def zip_bundle(bundle: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle).as_posix())
    return buf.getvalue()


def run_budget(config: dict) -> timedelta:
    """Whole minutes (the sandbox's idle_suspend resolution)."""
    minutes = float(config["run_minutes"]) + 2 * int(config["concurrent_users"]) / 60
    return timedelta(minutes=math.ceil(minutes)) + RUN_MARGIN


def relay_events(log_text: str, seen: int) -> tuple[int, dict | None]:
    """Print event lines not printed yet; return the new count and the final RUN event."""
    events = []
    for line in log_text.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("iwb") in ("event", "bootstrap"):
            events.append(parsed)
    for event in events[seen:]:
        print(json.dumps(event), flush=True)
    final = next((e for e in reversed(events)
                  if e.get("step") == "RUN" and e.get("status") in ("completed", "failed")
                  and "exit_code" in e.get("detail", {})), None)
    return len(events), final


def _sh(sandbox: Sandbox, script: str) -> str:
    return sandbox.exec(["bash", "-c", script]).stdout


class _Relay:
    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self.seen = 0
        self.log = ""

    def poll(self) -> tuple[str, dict | None]:
        """Return bootstrap's exit code ("" while running) and the final RUN event, if any."""
        rc = self.sandbox.read_text(BOOTSTRAP_RC).strip()
        self.log = self.sandbox.read_text(RUN_LOG)
        self.seen, final = relay_events(self.log, self.seen)
        return rc, final

    def tail(self) -> str:
        return self.log[-4000:]


def start(sandbox: Sandbox, bundle: Path) -> None:
    _sh(sandbox, f"mkdir -p {CODE_DIR}")
    sandbox.write_bytes(f"{WORK_DIR}/bundle.zip", zip_bundle(bundle))
    _sh(sandbox, f"cd {CODE_DIR} && unzip -q -o {WORK_DIR}/bundle.zip && : > {RUN_LOG} && : > {BOOTSTRAP_RC}")
    _sh(sandbox, f"cd {CODE_DIR} && setsid nohup bash -c 'bash bootstrap.sh config.json; echo $? > {BOOTSTRAP_RC}' "
                 f">> {RUN_LOG} 2>&1 < /dev/null &")


def wait(relay: _Relay, deadline: float) -> int:
    failures = 0
    while True:
        try:
            rc, final = relay.poll()
            failures = 0
        except SandboxError:
            failures += 1
            if failures >= MAX_POLL_FAILURES:
                raise
            time.sleep(POLL_SECONDS)
            continue
        if rc:
            if final and final["detail"].get("result"):
                try:
                    print(relay.sandbox.read_text(final["detail"]["result"]), flush=True)
                except SandboxError as exc:
                    sys.stderr.write(f"could not read {final['detail']['result']}: {exc}\n")
            if not final or int(rc) != int(final["detail"]["exit_code"]):
                sys.stderr.write(f"bootstrap exited ({rc}); log tail:\n{relay.tail()}\n")
                return int(rc) or 1
            return int(rc)
        if time.monotonic() > deadline:
            raise TimeoutError(f"run did not finish before the deadline; log tail:\n{relay.tail()}")
        time.sleep(POLL_SECONDS)


def stop(relay: _Relay) -> None:
    """Let the controller tear down before the sandbox is terminated."""
    sys.stderr.write("stopping the controller so it can drop any warehouse it created\n")
    try:
        _sh(relay.sandbox, f"kill -TERM $(cat {CONTROLLER_PID} 2>/dev/null) 2>/dev/null || true")
        deadline = time.monotonic() + STOP_GRACE.total_seconds()
        while time.monotonic() < deadline:
            if relay.poll()[0]:
                return
            time.sleep(POLL_SECONDS)
        sys.stderr.write(f"controller did not exit within {STOP_GRACE}; check for IWB_* warehouses\n")
    except SandboxError as exc:
        sys.stderr.write(f"could not stop the controller ({exc}); check for IWB_* warehouses\n")


def run(sandbox: Sandbox, bundle: Path, deadline: float) -> int:
    start(sandbox, bundle)
    relay = _Relay(sandbox)
    try:
        return wait(relay, deadline)
    except BaseException:
        stop(relay)
        raise


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--connection", required=True, help="client-side connections.toml entry")
    parser.add_argument("--role", help="role the sandbox (and so the benchmark) runs as")
    parser.add_argument("--results-stage", help="optional stage to mount writable for results (DB.SCHEMA.NAME)")
    parser.add_argument("--image", default="", help="catalog image; empty = deployment default")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    config = json.loads(args.config.read_text())
    budget = run_budget(config)
    results_stage = args.results_stage.lstrip("@") if args.results_stage else None
    env = {"IWB_RESULTS_DIR": RESULTS_MOUNT} if results_stage else {}
    mounts = ([StageMount.from_stage(results_stage, mount_path=RESULTS_MOUNT, readonly=False)]
              if results_stage else None)

    bundle = build_bundle(args.config)
    sandbox = None
    try:
        sandbox = Sandbox.create(
            connection=args.connection,
            image=args.image,
            memory=memory_tier(int(config["concurrent_users"])),
            env=env,
            stage_mounts=mounts,
            role=args.role,
            idle_suspend=budget + STOP_GRACE,
            tags={"app": "interactive-benchmark"},
        )
        print(json.dumps({"iwb": "launcher", "sandbox": sandbox.id, "role": args.role or "default"}), flush=True)
        return run(sandbox, bundle, time.monotonic() + budget.total_seconds())
    finally:
        if sandbox is not None:
            try:
                sandbox.terminate()
            except SandboxError as exc:
                sys.stderr.write(f"could not terminate sandbox {sandbox.id}: {exc}\n")
        shutil.rmtree(bundle, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
