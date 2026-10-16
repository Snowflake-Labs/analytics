"""Run one interactive benchmark in a Cortex Sandbox and print its events.

Reference client for a host that drives a sandbox through exec calls: create it as the
given role, start the controller detached, relay its `{"iwb": "event", ...}` lines until the
final RUN event, then print result.json.

With --image, the sandbox runs that benchmark image: only the config is uploaded, and the
image's controller/run.sh runs it. Without it, the sandbox runs the default image: the API,
Locust and controller sources from this checkout are uploaded with bootstrap.sh, which
installs them from pypi.org first and then runs the same run.sh.

Requires: pip install "snowflake-sandbox-python==0.2.2a4"   (Cortex Sandboxes, private preview)
See README.md in this directory.

Usage:
  python launch.py --config run.json --connection my-conn [--role ROLE] [--results-stage DB.SCHEMA.STAGE]
                   [--image /snowflake/images/snowflake_images/interactive-analytics/interactive-benchmark:TAG]
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
sys.path.insert(0, str(IMAGE_SOURCES / "controller"))
from iwb import budget  # noqa: E402  (dependency-free; shared with the controller)

WORK_DIR = "/var/tmp/iwb"
CODE_DIR = f"{WORK_DIR}/code"
RUN_LOG = f"{WORK_DIR}/run.log"
RUN_RC = f"{WORK_DIR}/run.rc"
CONFIG = f"{WORK_DIR}/config.json"
IMAGE_RUN = "/app/controller/run.sh"
CONTROLLER_PID = f"{WORK_DIR}/controller.pid"
STOP_FILE = f"{WORK_DIR}/stop"
RESULTS_MOUNT = "/iwb/results"
POLL_SECONDS = 15
MAX_POLL_FAILURES = 5
# Dependency install before the controller starts, without --image.
INSTALL_SECONDS = 600
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


def run_budget(config: dict, install: bool) -> timedelta:
    """Longer than the controller's worst case, so it times out and cleans up first; whole minutes
    (the sandbox's idle_suspend resolution)."""
    run_secs = budget.run_seconds(float(config["run_minutes"]))
    seconds = budget.worst_case_seconds(run_secs, int(config["concurrent_users"])) + install * INSTALL_SECONDS
    return timedelta(minutes=math.ceil(seconds / 60))


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
        """Return the run's exit code ("" while running) and the final RUN event, if any."""
        rc = self.sandbox.read_text(RUN_RC).strip()
        self.log = self.sandbox.read_text(RUN_LOG)
        self.seen, final = relay_events(self.log, self.seen)
        return rc, final

    def tail(self) -> str:
        return self.log[-4000:]


def start(sandbox: Sandbox, config: bytes, bundle: Path | None) -> None:
    """Start the run detached; with no bundle, the sandbox image is the benchmark image."""
    _sh(sandbox, f"mkdir -p {CODE_DIR}")
    if bundle is None:
        sandbox.write_bytes(CONFIG, config)
        command = f"bash {IMAGE_RUN} {CONFIG}"
    else:
        sandbox.write_bytes(f"{WORK_DIR}/bundle.zip", zip_bundle(bundle))
        _sh(sandbox, f"cd {CODE_DIR} && unzip -q -o {WORK_DIR}/bundle.zip")
        command = f"cd {CODE_DIR} && bash bootstrap.sh config.json"
    _sh(sandbox, f": > {RUN_LOG} && : > {RUN_RC}")
    _sh(sandbox, f"setsid nohup bash -c '{command}; echo $? > {RUN_RC}' >> {RUN_LOG} 2>&1 < /dev/null &")


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
                sys.stderr.write(f"run exited ({rc}); log tail:\n{relay.tail()}\n")
                return int(rc) or 1
            return int(rc)
        if time.monotonic() > deadline:
            raise TimeoutError(f"run did not finish before the deadline; log tail:\n{relay.tail()}")
        time.sleep(POLL_SECONDS)


def stop(relay: _Relay) -> None:
    """Let the controller tear down before the sandbox is terminated. SDK errors and further stop
    requests are ignored until STOP_GRACE ends: giving up early terminates the sandbox mid-teardown."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)
    sys.stderr.write("stopping the controller so it can drop any warehouse it created\n")
    deadline = time.monotonic() + STOP_GRACE.total_seconds()
    signalled = False
    error = None
    while time.monotonic() < deadline:
        try:
            if not signalled:
                _sh(relay.sandbox, f"touch {STOP_FILE}; kill -TERM $(cat {CONTROLLER_PID} 2>/dev/null) 2>/dev/null || true")
                signalled = True
            if relay.poll()[0]:
                return
        except SandboxError as exc:
            error = exc
        time.sleep(POLL_SECONDS)
    reason = f"last error: {error}" if error else "no exit"
    sys.stderr.write(f"controller did not finish teardown within {STOP_GRACE} ({reason}); check for IWB_* warehouses\n")


def run(sandbox: Sandbox, config: bytes, bundle: Path | None, deadline: float) -> int:
    start(sandbox, config, bundle)
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
    parser.add_argument("--image", help="benchmark image to run; default: this checkout's sources on the "
                                         "deployment's default sandbox image")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    config_bytes = args.config.read_bytes()
    config = json.loads(config_bytes)
    run_time = run_budget(config, install=not args.image)
    results_stage = args.results_stage.lstrip("@") if args.results_stage else None
    env = {"IWB_RESULTS_COPY_DIR": RESULTS_MOUNT} if results_stage else {}
    mounts = ([StageMount.from_stage(results_stage, mount_path=RESULTS_MOUNT, readonly=False)]
              if results_stage else None)

    bundle = None if args.image else build_bundle(args.config)
    sandbox = None
    try:
        sandbox = Sandbox.create(
            connection=args.connection,
            image=args.image or "",
            memory=memory_tier(int(config["concurrent_users"])),
            env=env,
            stage_mounts=mounts,
            role=args.role,
            idle_suspend=run_time + STOP_GRACE,
            tags={"app": "interactive-benchmark"},
        )
        print(json.dumps({"iwb": "launcher", "sandbox": sandbox.id, "role": args.role or "default"}), flush=True)
        return run(sandbox, config_bytes, bundle, time.monotonic() + run_time.total_seconds())
    finally:
        if sandbox is not None:
            try:
                sandbox.terminate()
            except SandboxError as exc:
                sys.stderr.write(f"could not terminate sandbox {sandbox.id}: {exc}\n")
        if bundle is not None:
            shutil.rmtree(bundle, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
