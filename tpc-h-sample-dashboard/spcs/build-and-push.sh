#!/usr/bin/env bash
# Build and push both container images to the SPCS image repository.
#
# BUILD_METHOD=spcs (default): builds server-side via
#   `snow spcs service build-image`, no local Docker daemon required.
# BUILD_METHOD=docker: local `docker build` + `docker push` (requires Docker
#   Desktop/buildx and `snow spcs image-registry login`).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

if [[ "$BUILD_METHOD" == "spcs" ]]; then
  spcs_build_image "$DASHBOARD_IMAGE" "spcs/dashboard" "api" "public"
  spcs_build_image "$LOCUST_IMAGE" "spcs/locust" "locust"

  echo "==> Done. Images pushed to ${DB}.${SCHEMA}.${IMAGE_REPO}:"
  echo "    $DASHBOARD_IMAGE:$IMAGE_TAG"
  echo "    $LOCUST_IMAGE:$IMAGE_TAG"
  exit 0
fi

echo "==> Logging into SPCS image registry via connection '$CONNECTION'"
snow spcs image-registry login --connection "$CONNECTION" --role "$ROLE"

DASHBOARD_REF="$(image_ref "$DASHBOARD_IMAGE")"
LOCUST_REF="$(image_ref "$LOCUST_IMAGE")"

echo "==> Building dashboard image: $DASHBOARD_REF"
docker build \
  --platform linux/amd64 \
  -f "$SCRIPT_DIR/dashboard/Dockerfile" \
  -t "$DASHBOARD_REF" \
  "$REPO_DIR"

echo "==> Pushing dashboard image"
docker push "$DASHBOARD_REF"

echo "==> Building locust image: $LOCUST_REF"
docker build \
  --platform linux/amd64 \
  -f "$SCRIPT_DIR/locust/Dockerfile" \
  -t "$LOCUST_REF" \
  "$REPO_DIR"

echo "==> Pushing locust image"
docker push "$LOCUST_REF"

echo "==> Done. Images:"
echo "    $DASHBOARD_REF"
echo "    $LOCUST_REF"

