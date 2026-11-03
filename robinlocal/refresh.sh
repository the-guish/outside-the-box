#!/usr/bin/env bash
# Downloads the zip again into the work volume and restarts Robinauts, with no image build.
set -euo pipefail
source "$(dirname "$0")/common.sh"

check_cloud
docker compose stop robinauts
docker compose run --rm --no-deps robinauts /scripts/entry.sh fetch
docker compose up -d robinauts
wait_for_robinauts
