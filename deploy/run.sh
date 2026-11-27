#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Manual execution helper for multitenant-elt production container
# Uses the pre-built GHCR image (zero local compilation or git clones needed)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Resolve clients volume whether running standalone in /opt/agency or inside repo
if [ -d "./clients" ]; then
    export CLIENTS_VOLUME="./clients"
elif [ -d "../clients" ]; then
    export CLIENTS_VOLUME="../clients"
else
    export CLIENTS_VOLUME="./clients"
fi

if [ $# -eq 0 ]; then
    cat <<EOF
multitenant-elt production manual runner

Usage: ./run.sh <command> [options]

Common Commands:
  ./run.sh pipeline                   # Run full pipeline for all active clients
  ./run.sh pipeline --client acme     # Run pipeline for a specific client
  ./run.sh auth meta                  # Run interactive Meta token wizard
  ./run.sh auth tiktok --client acme  # Run interactive TikTok OAuth v2 wizard
  ./run.sh dbt build                  # Run all dbt staging and mart models
  ./run.sh dbt test                   # Run dbt data quality assertions
  ./run.sh connector meta --client acme # Run standalone connector extraction
  ./run.sh cli --help                 # View complete CLI documentation

Note: All executions run in an isolated ephemeral container that self-destructs upon exit.
EOF
    exit 0
fi

exec docker compose -f compose.prod.yaml run --rm pipeline "$@"
