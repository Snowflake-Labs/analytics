from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from bench_core import config as _cfg
from bench_core.queries import query_label
from bench_core.types import QueryResult

_FLOAT_REL_TOL = 1e-4
_FLOAT_ABS_TOL = 1e-2


def load_expected_results_1gb() -> dict[str, dict[str, Any]]:
    """Load per-query expected output rows for SF1 (1GB) from the reference file."""
    data = json.loads(_cfg.EXPECTED_RESULTS_1GB_PATH.read_text())
    return {entry["query"]: entry["output"] for entry in data}


def _normalize_col_name(name: str) -> str:
    """Normalize a result column name for lookup (e.g. SUM(L_QTY) -> SUM_L_QTY)."""
    normalized = re.sub(r"[^A-Z0-9]", "_", name.upper())
    return re.sub(r"_+", "_", normalized).strip("_")


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _values_equal(actual: Any, expected: Any) -> bool:
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False

    actual_n = _normalize(actual)
    expected_n = _normalize(expected)

    if isinstance(actual_n, str) and isinstance(expected_n, str):
        return actual_n.rstrip() == expected_n.rstrip()

    if isinstance(expected_n, int) and not isinstance(expected_n, bool):
        if isinstance(actual_n, str) and actual_n.strip().lstrip("-").isdigit():
            return int(actual_n.strip()) == expected_n
        if isinstance(actual_n, float):
            return abs(actual_n - expected_n) < _FLOAT_ABS_TOL
        return actual_n == expected_n

    if (
        isinstance(expected_n, str)
        and isinstance(actual_n, int)
        and expected_n.strip().lstrip("-").isdigit()
    ):
        return actual_n == int(expected_n.strip())

    if isinstance(expected_n, bool) or isinstance(actual_n, bool):
        return actual_n == expected_n

    if isinstance(expected_n, float) or isinstance(actual_n, float):
        try:
            return abs(float(actual_n) - float(expected_n)) <= max(
                _FLOAT_ABS_TOL, _FLOAT_REL_TOL * abs(float(expected_n))
            )
        except (TypeError, ValueError):
            return False

    return actual_n == expected_n


def _row_dict(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i, col in enumerate(columns):
        value = row[i]
        out[col.upper()] = value
        out[_normalize_col_name(col)] = value
    return out


def _column_lookup_names(columns: list[str]) -> list[str]:
    names: list[str] = []
    for col in columns:
        upper = col.upper()
        normalized = _normalize_col_name(col)
        if upper not in names:
            names.append(upper)
        if normalized not in names:
            names.append(normalized)
    return names


def _resolve_column(row: dict[str, Any], columns: list[str], key: str) -> Any:
    """Resolve an expected column name to a value from the result row."""
    key_upper = key.upper()
    normalized_key = _normalize_col_name(key)
    if key_upper in row:
        return row[key_upper]
    if normalized_key in row:
        return row[normalized_key]

    suffix = f"_{key_upper}"
    matches = [name for name in _column_lookup_names(columns) if name.endswith(suffix)]
    if len(matches) == 1:
        return row[matches[0]]
    return None


def _row_matches(row: dict[str, Any], columns: list[str], expected: dict[str, Any]) -> bool:
    return all(
        _values_equal(_resolve_column(row, columns, key), exp_val)
        for key, exp_val in expected.items()
    )


def _find_matching_row(
    rows: list[tuple[Any, ...]], columns: list[str], expected: dict[str, Any]
) -> dict[str, Any] | None:
    for row in rows:
        actual = _row_dict(columns, row)
        if _row_matches(actual, columns, expected):
            return actual
    return None


def _mismatch_details(
    actual: dict[str, Any], columns: list[str], expected: dict[str, Any]
) -> list[str]:
    mismatches: list[str] = []
    for key, exp_val in expected.items():
        got = _resolve_column(actual, columns, key)
        if not _values_equal(got, exp_val):
            if got is None:
                mismatches.append(
                    f"{key}: column not found in result (available: {', '.join(columns)})"
                )
            else:
                mismatches.append(f"{key}: expected {exp_val!r}, got {got!r}")
    return mismatches


def validate_query_output(
    *,
    query_stem: str,
    columns: list[str],
    rows: list[tuple[Any, ...]],
    expected_by_query: dict[str, dict[str, Any]],
) -> tuple[str, str | None]:
    """Validate one query's result set against the SF1 reference row.

    The reference file defines one row that must appear in the result set.
    Extra rows are allowed.
    """
    label = query_label(query_stem)
    expected = expected_by_query.get(label)
    if expected is None:
        return "SKIP", f"no expected output for {label}"

    if not rows:
        return "FAIL", "query returned no rows"

    match = _find_matching_row(rows, columns, expected)
    if match is not None:
        return "PASS", None

    best_mismatches: list[str] = []
    for row in rows:
        actual = _row_dict(columns, row)
        mismatches = _mismatch_details(actual, columns, expected)
        if not mismatches:
            return "FAIL", "result row did not match expected output"
        if not best_mismatches or len(mismatches) < len(best_mismatches):
            best_mismatches = mismatches

    if len(rows) == 1:
        return "FAIL", "; ".join(best_mismatches)

    detail = "; ".join(best_mismatches[:3])
    if len(best_mismatches) > 3:
        detail += f"; … ({len(best_mismatches) - 3} more)"
    return "FAIL", f"no row matched expected output for {label} ({detail})"


def apply_validation(
    results: list[QueryResult], expected_by_query: dict[str, dict[str, Any]]
) -> None:
    """Attach validation status to each successful result row."""
    for result in results:
        if result["status"] != "OK":
            result["validation"] = "SKIP"
            result["validation_error"] = None
            continue

        validation, error = validate_query_output(
            query_stem=result["query"],
            columns=result.get("result_columns") or [],
            rows=result.get("result_rows") or [],
            expected_by_query=expected_by_query,
        )
        result["validation"] = validation
        result["validation_error"] = error
