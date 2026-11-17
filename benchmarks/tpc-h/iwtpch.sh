#!/usr/bin/env bash
set -euo pipefail

# Thin wrapper around the iw-tpch CLI (same as `uv run iw-tpch`).
# Forwards all arguments unchanged.
#
# Usage:
#   ./iwtpch.sh setup --scale 10
#   ./iwtpch.sh setup --scale 10 --tables-type iceberg --warehouse-type standard
#   ./iwtpch.sh run --warehouse-type interactive --tables-type interactive --scale 10
#   ./iwtpch.sh run --warehouse-type standard --tables-type iceberg --scale 100 --queries 2,11,15
#   ./iwtpch.sh teardown
#
# Run `./iwtpch.sh --help` for full CLI options.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

exec uv run iw-tpch "$@"
