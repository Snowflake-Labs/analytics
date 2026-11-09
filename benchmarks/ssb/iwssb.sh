#!/usr/bin/env bash
set -euo pipefail

# Thin wrapper around the iw-ssb CLI (same as `uv run iw-ssb`).
# Forwards all arguments unchanged.
#
# Usage:
#   ./iwssb.sh setup --scale 10
#   ./iwssb.sh setup --scale 10 --tables-type iceberg --warehouse-type standard
#   ./iwssb.sh run --warehouse-type interactive --tables-type interactive --scale 10
#   ./iwssb.sh run --warehouse-type standard --tables-type iceberg --scale 100 --queries 1.1,2.3,4
#   ./iwssb.sh teardown
#
# Run `./iwssb.sh --help` for full CLI options.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

exec uv run iw-ssb "$@"
