#!/usr/bin/env bash
set -Eeuo pipefail

PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

COMPOSE_FILE="${PKG}/compose.yaml"
ENV_FILE="${PKG}/.env"
HEALTH_URL="http://127.0.0.1:5801/health"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "Missing Compose file: ${COMPOSE_FILE}" >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing environment file: ${ENV_FILE}" >&2
    echo "Create it from .env.example and configure MODEL_PATH." >&2
    exit 1
fi

mkdir -p \
    "${PKG}/cache/huggingface" \
    "${PKG}/logs"

export PYTHONPATH="$(rbnx path robonix-api):${PKG}:${PYTHONPATH:-}"

COMPOSE_CMD=(
    docker compose
    --project-directory "${PKG}"
    --env-file "${ENV_FILE}"
    -f "${COMPOSE_FILE}"
)

SKILL_PID=""

cleanup() {
    local exit_status=$?

    # 防止 exit 再次触发 cleanup
    trap - EXIT INT TERM
    set +e

    if [[ -n "${SKILL_PID}" ]] && kill -0 "${SKILL_PID}" 2>/dev/null; then
        echo "Stopping InternVLA navigation Skill..."
        kill -TERM "${SKILL_PID}" 2>/dev/null
        wait "${SKILL_PID}" 2>/dev/null
    fi

    echo "Stopping InternVLA container..."
    "${COMPOSE_CMD[@]}" down --remove-orphans

    exit "${exit_status}"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "${PKG}"

echo "Starting InternVLA container..."
"${COMPOSE_CMD[@]}" up -d --remove-orphans

echo "Waiting for InternVLA server at ${HEALTH_URL}..."

READY=false

# 最长等待 10 分钟：120 × 5 秒
for ((attempt = 1; attempt <= 120; attempt++)); do
    if curl \
        --fail \
        --silent \
        --show-error \
        --max-time 3 \
        "${HEALTH_URL}" >/dev/null 2>&1; then
        READY=true
        echo "InternVLA server is ready."
        break
    fi

    # 容器若提前退出，不必继续等待
    if [[ -z "$("${COMPOSE_CMD[@]}" ps --status running -q internvla-server)" ]]; then
        echo "InternVLA container exited during startup." >&2
        "${COMPOSE_CMD[@]}" logs --tail=100 internvla-server >&2
        exit 1
    fi

    if ((attempt % 12 == 0)); then
        echo "Still waiting for InternVLA server ($((attempt * 5)) seconds)..."
    fi

    sleep 5
done

if [[ "${READY}" != true ]]; then
    echo "InternVLA server did not become ready within 600 seconds." >&2
    "${COMPOSE_CMD[@]}" logs --tail=100 internvla-server >&2
    exit 1
fi

echo "Starting InternVLA navigation Skill..."
python3 -u -m internvla_navigation.main &
SKILL_PID=$!

set +e
wait "${SKILL_PID}"
SKILL_STATUS=$?
set -e

SKILL_PID=""
exit "${SKILL_STATUS}"