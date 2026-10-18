#!/bin/bash
set -eo pipefail

# If the command starts with an option, prepend python
if [ "${1#-}" != "$1" ]; then
    set -- python -m agency_analytics.cli "$@"
fi

# Subcommand shortcuts
case "$1" in
    pipeline|cli)
        shift
        exec python -m agency_analytics.cli "$@"
        ;;
    dbt)
        shift
        cd /app/src/dbt_project
        exec dbt "$@"
        ;;
    connector)
        shift
        conn="$1"
        shift
        exec python "src/connectors/run_${conn}.py" "$@"
        ;;
esac

exec "$@"
