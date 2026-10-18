#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Production Orchestration Script (No make dependency required)
# Developmi / multitenant-elt standard
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

usage() {
    cat <<EOF
Usage: ./scripts/prod.sh <command> [options]

Commands:
  up [--with-metabase]  Start production stack (Postgres + Pipeline, opt-in Metabase)
  down                  Stop all services cleanly
  status                Check health and status of all services
  bootstrap             Bootstrap dbt monitoring chain on fresh DB
  pipeline              Execute scheduled ELT pipeline for all active clients
  logs [service]        Tail logs (e.g. ./scripts/prod.sh logs pipeline)

Requirements:
  Docker Engine 24+ and Docker Compose v2. No make required.
EOF
    exit 1
}

cmd_up() {
    local with_metabase=false
    if [ "${1:-}" = "--with-metabase" ]; then
        with_metabase=true
    fi

    echo "==> Ensuring Docker networks exist..."
    ./scripts/setup-networks.sh

    echo "==> Starting Postgres service..."
    docker compose -f services/db/compose.yaml up -d

    echo "==> Waiting 3s for DB initialization..."
    sleep 3

    echo "==> Starting Pipeline worker..."
    docker compose -f services/pipeline/compose.yaml up -d

    if [ "$with_metabase" = true ]; then
        echo "==> Starting Metabase service..."
        docker compose --profile metabase -f services/db/compose.yaml -f services/metabase/compose.yaml up -d metabase
    fi

    echo "==> Stack started successfully. Run './scripts/prod.sh status' to verify health."
}

cmd_down() {
    echo "==> Stopping all services..."
    docker compose --profile metabase -f services/db/compose.yaml -f services/metabase/compose.yaml down 2>/dev/null || true
    docker compose -f services/pipeline/compose.yaml down 2>/dev/null || true
    docker compose -f services/db/compose.yaml down 2>/dev/null || true
    echo "==> All services stopped."
}

cmd_status() {
    echo "==> Container Status & Health:"
    docker ps --filter "name=agency_" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

cmd_bootstrap() {
    echo "==> Waiting for agency_postgres and agency_pipeline to be healthy..."
    for c in agency_postgres agency_pipeline; do
        ok=""
        for i in $(seq 1 60); do
            s="$(docker inspect --format='{{.State.Health.Status}}' "$c" 2>/dev/null || echo missing)"
            if [ "$s" = "healthy" ]; then ok=1; break; fi
            sleep 2
        done
        if [ -z "$ok" ]; then echo "ERROR: $c is not healthy ($s)"; exit 1; fi
        echo "    $c: healthy"
    done

    echo "==> Running dbt monitoring chain bootstrap..."
    chain="$(docker exec -w /app/src agency_pipeline python3 -c 'from agency_analytics.pipeline_plan import MONITORING_CHAIN; print(" ".join(MONITORING_CHAIN))')" || exit 1
    docker exec -w /app/src/dbt_project agency_pipeline dbt build --select $chain --profiles-dir .
    echo "==> Bootstrap completed successfully."
}

cmd_pipeline() {
    echo "==> Triggering production pipeline execution..."
    ./scripts/pipeline.sh
}

cmd_logs() {
    local service="${1:-}"
    if [ "$service" = "db" ] || [ "$service" = "postgres" ]; then
        docker compose -f services/db/compose.yaml logs -f
    elif [ "$service" = "pipeline" ]; then
        docker compose -f services/pipeline/compose.yaml logs -f
    elif [ "$service" = "metabase" ]; then
        docker compose --profile metabase -f services/db/compose.yaml -f services/metabase/compose.yaml logs -f metabase
    else
        docker compose -f services/db/compose.yaml logs -f &
        docker compose -f services/pipeline/compose.yaml logs -f &
        wait
    fi
}

case "${1:-}" in
    up)
        shift
        cmd_up "$@"
        ;;
    down)
        cmd_down
        ;;
    status)
        cmd_status
        ;;
    bootstrap)
        cmd_bootstrap
        ;;
    pipeline)
        cmd_pipeline
        ;;
    logs)
        shift
        cmd_logs "$@"
        ;;
    *)
        usage
        ;;
esac
