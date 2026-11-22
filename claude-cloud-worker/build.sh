#!/bin/sh
# Builds the worker's image, claude-cloud-worker, from Robinauts at the commit robinauts.pin
# names and from this folder.
#
#     ./build.sh [an image name]
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker buildx build --tag "${1:-claude-cloud-worker}" \
    --build-context "robinauts=$(cat "$here/robinauts.pin")" "$here"
