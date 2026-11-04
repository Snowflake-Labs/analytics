#!/usr/bin/env bash
# Shared helpers sourced by all orchestration scripts.
# Loads config.env and defines wrappers around `snow sql` and `snow spcs`.

set -euo pipefail

SPCS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SPCS_DIR/.." && pwd)"

# shellcheck disable=SC1091
source "$SPCS_DIR/config.env"

: "${CONNECTION:?CONNECTION must be set in config.env}"
: "${DB:?DB must be set}"
: "${SCHEMA:?SCHEMA must be set}"

export CONNECTION DB SCHEMA IMAGE_REPO ROLE DEPLOY_WAREHOUSE \
       SOLUTION_NAME \
       DASHBOARD_COMPUTE_POOL DASHBOARD_INSTANCE_FAMILY \
       DASHBOARD_MIN_NODES DASHBOARD_MAX_NODES \
       LOCUST_COMPUTE_POOL LOCUST_INSTANCE_FAMILY \
       LOCUST_MIN_NODES LOCUST_MAX_NODES \
       DASHBOARD_SERVICE LOCUST_API_SERVICE LOCUST_SERVICE \
       DASHBOARD_IMAGE LOCUST_IMAGE IMAGE_TAG \
       DASHBOARD_DATABASE DASHBOARD_ROLE DASHBOARD_WAREHOUSE \
       DASHBOARD_DEFAULT_SCALE DASHBOARD_PORT \
       DASHBOARD_WORKERS DASHBOARD_POOL_SIZE DASHBOARD_POOL_WARMUP \
       DASHBOARD_POOL_ACQUIRE_TIMEOUT \
       DASHBOARD_CPU_REQUEST DASHBOARD_CPU_LIMIT \
       DASHBOARD_MEMORY_REQUEST DASHBOARD_MEMORY_LIMIT \
       DASHBOARD_MIN_INSTANCES DASHBOARD_MAX_INSTANCES \
       LOCUST_HOST LOCUST_WEB_PORT LOCUST_USERS LOCUST_SPAWN \
       LOCUST_HEADLESS LOCUST_RUN_TIME LOCUST_WAREHOUSE LOCUST_SCALE \
       DASHBOARD_LOCUST_URL \
       BUILD_METHOD BUILD_COMPUTE_POOL BUILD_EAI_NAME

# Defaults for tuning knobs that may be missing on older config.env files.
: "${DASHBOARD_WORKERS:=4}"
: "${DASHBOARD_POOL_SIZE:=40}"
: "${DASHBOARD_POOL_WARMUP:=10}"
: "${DASHBOARD_POOL_ACQUIRE_TIMEOUT:=30}"
: "${DASHBOARD_CPU_REQUEST:=2000m}"
: "${DASHBOARD_CPU_LIMIT:=4000m}"
: "${DASHBOARD_MEMORY_REQUEST:=2Gi}"
: "${DASHBOARD_MEMORY_LIMIT:=4Gi}"
: "${DASHBOARD_MIN_INSTANCES:=1}"
: "${DASHBOARD_MAX_INSTANCES:=4}"
# BUILD_METHOD=docker (default) uses local `docker build`/`docker push`.
# BUILD_METHOD=spcs builds images server-side via `snow spcs service
# build-image`, no local Docker daemon required.
: "${BUILD_METHOD:=docker}"
: "${BUILD_COMPUTE_POOL:=$DASHBOARD_COMPUTE_POOL}"
# Space-separated external access integration names the build-image job
# needs for network egress (uv/pip install, apt-get, curl). Required in
# practice for BUILD_METHOD=spcs — the build job has no internet access
# without one. Leave empty only if your account allows unrestricted egress.
: "${BUILD_EAI_NAME:=}"

if [[ "$BUILD_METHOD" == "spcs" && -z "$BUILD_EAI_NAME" ]]; then
  echo "Warning: BUILD_EAI_NAME is empty while BUILD_METHOD=spcs." >&2
  echo "The SPCS build job will have no network access and will likely fail" >&2
  echo "if the Dockerfile needs to fetch packages (apt-get, pip, uv, curl)." >&2
  echo "Set BUILD_EAI_NAME in config.env or switch to BUILD_METHOD=docker." >&2
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

# Compares two X.Y.Z version strings. Returns 0 (true) if $1 >= $2.
version_ge() {
  local v1="$1" v2="$2"
  [[ "$v1" == "$v2" ]] && return 0
  local IFS=.
  local -a a=($v1) b=($v2)
  local i
  for i in 0 1 2; do
    local ai="${a[$i]:-0}" bi="${b[$i]:-0}"
    if (( 10#$ai > 10#$bi )); then return 0; fi
    if (( 10#$ai < 10#$bi )); then return 1; fi
  done
  return 0
}

# `snow spcs service build-image` was added (experimental) in 3.16.0; below
# that the subcommand doesn't exist at all, so hard-require it for
# BUILD_METHOD=spcs. 3.18.0 additionally fixed a SQL-injection bug in
# `service create/execute-job/upgrade` when a spec YAML contains a `$$`
# sequence (we call create/upgrade on every deploy regardless of
# BUILD_METHOD) and a build-image bug on Azure accounts using
# SNOWFLAKE_FULL stage encryption — recommend it unconditionally.
check_snow_cli_version() {
  local min="3.16.0" recommended="3.18.0"
  local version
  version="$(snow --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  if [[ -z "$version" ]]; then
    echo "Warning: could not parse snow CLI version from 'snow --version'; skipping version check." >&2
    return
  fi
  if [[ "$BUILD_METHOD" == "spcs" ]] && ! version_ge "$version" "$min"; then
    echo "Error: snow CLI $version is too old. 'snow spcs service build-image' (BUILD_METHOD=spcs) requires >= $min." >&2
    echo "Upgrade: https://docs.snowflake.com/en/developer-guide/snowflake-cli/installation/installation" >&2
    exit 1
  fi
  if ! version_ge "$version" "$recommended"; then
    echo "Warning: snow CLI $version works, but $recommended+ is recommended (fixes a spec-YAML SQL-injection edge case in service create/upgrade, and a build-image bug on Azure accounts)." >&2
  fi
}

require_cmd snow
check_snow_cli_version
require_cmd envsubst

if [[ "$BUILD_METHOD" == "docker" ]]; then
  require_cmd docker
fi

# snow spcs service build-image is experimental and hidden unless the feature
# flag is enabled. Enable it for the duration of this process rather than
# requiring every user to edit config.toml.
export SNOWFLAKE_CLI_FEATURES_ENABLE_SPCS_BUILD_IMAGE=true

# Run a SQL statement against $CONNECTION and print JSON output.
snow_sql() {
  snow sql --connection "$CONNECTION" --format json "$@"
}

# Run a SQL statement quietly and return the raw stdout.
snow_sql_quiet() {
  snow sql --connection "$CONNECTION" --silent --format json "$@"
}

# Run a SQL script (heredoc on stdin) suppressing normal table output.
# Only prints "SQL error:" plus the captured output on failure.
snow_sql_run() {
  local label="${1:-SQL}"
  local out
  local rc=0
  out="$(snow sql --connection "$CONNECTION" -i 2>&1)" || rc=$?
  if (( rc != 0 )); then
    echo "SQL error while running: $label" >&2
    echo "$out" >&2
    return "$rc"
  fi
  return 0
}

# Fetch the registry hostname for this account.
registry_url() {
  snow spcs image-registry url --connection "$CONNECTION" --role "$ROLE" 2>/dev/null | tr -d '"'
}

# Full image reference including registry.
image_ref() {
  local image_name="$1"
  local reg
  reg="$(registry_url)"
  local db_lower schema_lower repo_lower
  db_lower="$(echo "$DB" | tr '[:upper:]' '[:lower:]')"
  schema_lower="$(echo "$SCHEMA" | tr '[:upper:]' '[:lower:]')"
  repo_lower="$(echo "$IMAGE_REPO" | tr '[:upper:]' '[:lower:]')"
  echo "${reg}/${db_lower}/${schema_lower}/${repo_lower}/${image_name}:${IMAGE_TAG}"
}

# Render a spec yaml with env vars substituted.
render_spec() {
  local spec="$1"
  envsubst < "$spec"
}

# Thin wrapper around `snow spcs ...` with connection/role/database/schema
# pre-filled so every call is unambiguous about which schema it targets.
spcs() {
  snow spcs "$@" --connection "$CONNECTION" --role "$ROLE" \
    --database "$DB" --schema "$SCHEMA"
}

# Create a compute pool if it doesn't exist (idempotent).
spcs_compute_pool_create() {
  local pool="$1" family="$2" min_nodes="$3" max_nodes="$4"
  spcs compute-pool create "$pool" \
    --family "$family" \
    --min-nodes "$min_nodes" \
    --max-nodes "$max_nodes" \
    --auto-resume \
    --if-not-exists
  spcs compute-pool resume "$pool" 2>/dev/null || true
}

# Create an image repository if it doesn't exist (idempotent).
spcs_image_repo_create() {
  local repo="$1"
  spcs image-repository create "$repo" --if-not-exists
}

# Run `snow spcs service <create|upgrade>` with --spec-path pointed at a
# temporary file that is cleaned up automatically on exit.
spcs_apply_spec() {
  local action="$1" svc="$2" spec_content="$3"
  shift 3
  local tmpspec rc
  tmpspec="$(mktemp)"
  printf '%s\n' "$spec_content" > "$tmpspec"
  rc=0
  snow spcs service "$action" "$svc" --spec-path "$tmpspec" \
    --connection "$CONNECTION" --role "$ROLE" --database "$DB" --schema "$SCHEMA" "$@" || rc=$?
  rm -f -- "$tmpspec"
  return "$rc"
}

# Create-or-upgrade a service in place: create if missing, otherwise upgrade
# the running service's spec, then reconcile min/max instances.
spcs_service_upsert() {
  local svc="$1" pool="$2" spec_file="$3" min_inst="${4:-1}" max_inst="${5:-1}"
  local rendered
  rendered="$(render_spec "$spec_file")"

  spcs_apply_spec create "$svc" "$rendered" \
    --compute-pool "$pool" \
    --min-instances "$min_inst" \
    --max-instances "$max_inst" \
    --comment 'Managed by dashboard/spcs/' \
    --if-not-exists

  spcs_apply_spec upgrade "$svc" "$rendered"

  spcs service set "$svc" --min-instances "$min_inst" --max-instances "$max_inst"
}

# Build one image server-side with `snow spcs service build-image` and push it
# to $IMAGE_REPO. `--build-context-dir` requires a file literally named
# `Dockerfile` at its root, so stage a scoped temp context: the image's
# Dockerfile plus only the repo-root-relative paths it COPYs.
#
# extra_paths: repo-root-relative files/dirs the Dockerfile COPYs besides its
# own spcs/<image_name>/ directory (e.g. "api" "public").
spcs_build_image() {
  local image_name="$1" dockerfile_dir="$2"
  shift 2
  local extra_paths=("$@")

  local ctx
  ctx="$(mktemp -d)"
  trap 'rm -rf "$ctx"' RETURN

  cp "$REPO_DIR/$dockerfile_dir/Dockerfile" "$ctx/Dockerfile"
  mkdir -p "$ctx/$dockerfile_dir"
  cp "$REPO_DIR/$dockerfile_dir/entrypoint.sh" "$ctx/$dockerfile_dir/"
  local p
  for p in "${extra_paths[@]}"; do
    mkdir -p "$(dirname "$ctx/$p")"
    cp -r "$REPO_DIR/$p" "$ctx/$p"
  done

  local eai_args=()
  local eai
  for eai in $BUILD_EAI_NAME; do
    eai_args+=(--eai-name "$eai")
  done

  echo "==> Building $image_name server-side via snow spcs service build-image"
  spcs service build-image \
    --compute-pool "$BUILD_COMPUTE_POOL" \
    --image-repository "${DB}.${SCHEMA}.${IMAGE_REPO}" \
    --image-name "$image_name" \
    --image-tag "$IMAGE_TAG" \
    --build-context-dir "$ctx" \
    "${eai_args[@]+"${eai_args[@]}"}"
}
