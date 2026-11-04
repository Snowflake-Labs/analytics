#!/usr/bin/env bash
# Create the external access integration (EAI) needed for BUILD_METHOD=spcs.
#
# The SPCS build job runs in a network-isolated environment. This script
# creates a network rule and EAI that allow outbound HTTPS to the package
# registries the Dockerfiles depend on (Docker Hub, PyPI, Ubuntu apt).
#
# Prerequisites:
#   - The role in config.env must have CREATE INTEGRATION privilege, or
#     run this script with ROLE=ACCOUNTADMIN in config.env.
#
# After running this script, set BUILD_EAI_NAME in config.env to the
# integration name printed at the end.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

EAI_NAME="${1:-${SOLUTION_NAME}_BUILD_EAI}"
RULE_NAME="${EAI_NAME}_RULE"

echo "==> Creating network rule $DB.$SCHEMA.$RULE_NAME"
snow_sql_run "create network rule" <<EOF
USE ROLE $ROLE;
USE DATABASE $DB;
USE SCHEMA $SCHEMA;

CREATE OR REPLACE NETWORK RULE $RULE_NAME
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = (
    'registry-1.docker.io',
    'auth.docker.io',
    'production.cloudflare.docker.com',
    'pypi.org',
    'files.pythonhosted.org',
    'archive.ubuntu.com',
    'security.ubuntu.com'
  );
EOF

echo "==> Creating external access integration $EAI_NAME"
snow_sql_run "create EAI" <<EOF
USE ROLE $ROLE;

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION $EAI_NAME
  ALLOWED_NETWORK_RULES = ($DB.$SCHEMA.$RULE_NAME)
  ENABLED = TRUE;
EOF

echo
echo "Done. Set the following in config.env:"
echo "  BUILD_EAI_NAME=$EAI_NAME"
