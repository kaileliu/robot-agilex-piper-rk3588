#!/usr/bin/bash

set -Eeuo pipefail

SKILL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

docker compose \
    --project-directory "${SKILL_DIR}" \
    --env-file "${SKILL_DIR}/.env" \
    -f "${SKILL_DIR}/compose.yaml" \
    down \
    --remove-orphans