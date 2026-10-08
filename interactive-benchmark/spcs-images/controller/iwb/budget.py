"""Worst-case controller durations. Dependency-free: the sandbox launcher imports it to outlast the controller."""

STARTED_TIMEOUT_SECONDS = 900
API_READY_TIMEOUT_SECONDS = 600
# Validation, compile checks, warm-up, measurement and teardown.
OTHER_STEPS_SECONDS = 900


def run_seconds(run_minutes: float) -> int:
    return max(1, round(run_minutes * 60))


def locust_timeout_seconds(run_secs: int, users: int) -> int:
    """API readiness, the 1-minute baseline, the run, ramp-up of both phases, and shutdown slack."""
    return API_READY_TIMEOUT_SECONDS + 60 + run_secs + 2 * users + 600


def worst_case_seconds(run_secs: int, users: int) -> int:
    # The warehouse is waited on twice: after resume and after attaching tables.
    return 2 * STARTED_TIMEOUT_SECONDS + locust_timeout_seconds(run_secs, users) + OTHER_STEPS_SECONDS
