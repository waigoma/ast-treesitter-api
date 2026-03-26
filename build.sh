#!/usr/bin/env bash
set -euo pipefail

# Build local image (same pattern as embedding-api compose override).
docker compose -f compose.yml -f compose.build.yml build
