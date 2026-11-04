#!/usr/bin/env bash
# List all SPCS resources created by deploy.sh: image registry, repositories,
# services, and compute pools.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

echo "=== SPCS Resources for ${DB}.${SCHEMA} ==="
echo

echo "--- Image Registry URL ---"
registry_url
echo

echo "--- Image Repositories ---"
spcs image-repository list --format json 2>/dev/null \
  | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows:
    print("  (none)")
else:
    for r in rows:
        name = r.get("name")
        url = r.get("repository_url") or ""
        print(f"  {name:30s} {url}")
'
echo

echo "--- Services ---"
spcs service list --format json 2>/dev/null \
  | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows:
    print("  (none)")
else:
    for r in rows:
        name = r.get("name")
        status = r.get("status") or "?"
        pool = r.get("compute_pool") or ""
        print(f"  {name:30s} {status:12s} pool={pool}")
'
echo

echo "--- Compute Pools ---"
spcs compute-pool list --like "${SOLUTION_NAME}_BENCH%" --format json 2>/dev/null \
  | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows:
    print("  (none)")
else:
    for r in rows:
        name = r.get("name")
        state = r.get("state") or "?"
        family = r.get("instance_family") or ""
        min_n = r.get("min_nodes") or ""
        max_n = r.get("max_nodes") or ""
        print(f"  {name:35s} {state:12s} {family} (nodes: {min_n}-{max_n})")
'
echo

