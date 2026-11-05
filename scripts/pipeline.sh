#!/bin/bash
set -euo pipefail

# ─── Config & Telegram Fallback ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# Telegram keys: host env wins; guarded key-targeted fallback to root ./.env
# (no full source, no set -a, no export - no secret pollution of the process env).
if { [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; } && [ -f ./.env ]; then
  [ -z "$TELEGRAM_BOT_TOKEN" ] && TELEGRAM_BOT_TOKEN=$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' ./.env | head -n1 | tr -d '\r')
  [ -z "$TELEGRAM_CHAT_ID" ] && TELEGRAM_CHAT_ID=$(sed -n 's/^TELEGRAM_CHAT_ID=//p' ./.env | head -n1 | tr -d '\r')
fi

# ─── Host Health Check ───────────────────────────────────────────────────────
check_container() {
  local name=$1
  local status
  status=$(docker inspect --format='{{.State.Health.Status}}' "$name" 2>/dev/null || echo "not found")
  if [ "$status" != "healthy" ]; then
    echo "ERROR: Container $name unhealthy (status: $status). Aborting."
    exit 1
  fi
}

check_container "agency_postgres"
check_container "agency_pipeline"

# ─── Consolidated Python Pipeline Execution ─────────────────────────────────
# Executes the centralized Python orchestrator inside the container in a single
# process, eliminating 40+ docker exec invocations and bash SQL interpolation.
docker exec -i \
  -e PYTHONPATH=/app/src \
  ${TELEGRAM_BOT_TOKEN:+-e TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN"} \
  ${TELEGRAM_CHAT_ID:+-e TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID"} \
  agency_pipeline \
  python3 -m agency_analytics.pipeline_runner "$@"
