#!/usr/bin/env bash
# Print service status and public ingress URLs.
#
# Usage:
#   status.sh              print concise status for all services
#   status.sh --wait       poll until all services are ready for connections
#   status.sh --urls-only  print only "<service>: https://<url>" lines

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

MODE="${1:-full}"

# Fetch per-container status via `snow spcs service list-containers`.
service_info() {
  local svc="$1"
  spcs service list-containers "$svc" --format json 2>/dev/null \
    | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
for r in rows or []:
    status = r.get("status") or "UNKNOWN"
    msg = r.get("message") or ""
    print(f"{status}\t{msg}")
'
}

# Summarize: READY when all containers are READY, otherwise report actual state.
service_status() {
  local svc="$1"
  local info
  info="$(service_info "$svc" 2>/dev/null)" || info="UNKNOWN\tservice not found"
  if [[ -z "$info" ]]; then
    echo "NOT_FOUND"
    return
  fi
  # Take the first container's status (single-container services)
  echo "$info" | head -1 | cut -f1
}

service_message() {
  local svc="$1"
  local info
  info="$(service_info "$svc" 2>/dev/null)" || true
  if [[ -n "$info" ]]; then
    echo "$info" | head -1 | cut -f2
  fi
}

service_url() {
  local svc="$1"
  spcs service list-endpoints "$svc" --format json 2>/dev/null \
    | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for r in rows or []:
    name = r.get("name") or r.get("NAME")
    url  = r.get("ingress_url") or r.get("INGRESS_URL")
    if name == "web" and url:
        print(url)
        break
' || true
}

print_service_status() {
  local svc="$1"
  local status msg url
  status="$(service_status "$svc")"
  msg="$(service_message "$svc")"
  url="$(service_url "$svc")"

  local icon
  case "$status" in
    READY)   icon="✓" ;;
    PENDING) icon="⏳" ;;
    FAILED)  icon="✗" ;;
    *)       icon="?" ;;
  esac

  printf "  %s %-25s %s" "$icon" "$svc" "$status"
  if [[ -n "$msg" && "$status" != "READY" ]]; then
    printf "  (%s)" "$msg"
  fi
  echo
  if [[ -n "$url" && "$status" == "READY" ]]; then
    printf "    └─ https://%s\n" "$url"
  fi
}

wait_ready() {
  local svc="$1"
  local tries=60
  local status
  while (( tries > 0 )); do
    status="$(service_status "$svc")"
    local msg
    msg="$(service_message "$svc")"
    printf "\r  ⏳ %-25s %s" "$svc" "${status}${msg:+ ($msg)}"
    if [[ "$status" == "READY" ]]; then
      local url
      url="$(service_url "$svc")"
      printf "\r  ✓  %-25s READY\n" "$svc"
      [[ -n "$url" ]] && printf "    └─ https://%s\n" "$url"
      return 0
    fi
    if [[ "$status" == "FAILED" ]]; then
      printf "\r  ✗  %-25s FAILED (%s)\n" "$svc" "$msg"
      echo "     Run ./logs.sh to investigate." >&2
      return 1
    fi
    sleep 10
    tries=$((tries-1))
    # Clear to end of line before next update
    printf "\033[K"
  done
  printf "\r  ✗  %-25s TIMEOUT (did not reach READY in 10 min)\n" "$svc"
  return 1
}

case "$MODE" in
  --wait)
    echo "Waiting for services to be ready..."
    wait_ready "$DASHBOARD_SERVICE"
    wait_ready "$LOCUST_API_SERVICE"
    wait_ready "$LOCUST_SERVICE"
    echo
    echo "All services ready."
    ;;
  --urls-only)
    du="$(service_url "$DASHBOARD_SERVICE")"
    au="$(service_url "$LOCUST_API_SERVICE")"
    lu="$(service_url "$LOCUST_SERVICE")"
    [[ -n "$du" ]] && echo "dashboard:      https://$du"
    [[ -n "$au" ]] && echo "locust-api:     https://$au"
    [[ -n "$lu" ]] && echo "locust:         https://$lu"
    ;;
  *)
    echo "Services in ${DB}.${SCHEMA}:"
    echo
    print_service_status "$DASHBOARD_SERVICE"
    print_service_status "$LOCUST_API_SERVICE"
    print_service_status "$LOCUST_SERVICE"
    echo
    ;;
esac
