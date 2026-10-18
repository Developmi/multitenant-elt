# Development Guide - multitenant-elt

---

## Overview

This guide explains how to develop, test, and extend `multitenant-elt`. It covers adding new platform connectors, configuring client YAML definitions, building dbt models, and adhering to the quality gate.

---

## Table of contents

- [Local environment setup](#local-environment-setup)
- [Adding a new connector](#adding-a-new-connector)
- [Client YAML configuration](#client-yaml-configuration)
- [dbt modeling workflow](#dbt-modeling-workflow)
- [Testing & Quality gate](#testing--quality-gate)

---

## Local environment setup

The project standardizes on **`uv`** as its sole Python package manager and **Docker Compose** for infrastructure.

```bash
# 1. Clone repository
git clone https://github.com/Developmi/multitenant-elt.git
cd multitenant-elt

# 2. Sync dependencies
uv sync

# 3. Configure environment
cp .env.example .env
# Edit .env with your local credentials
```

---

## Adding a new connector

All data extraction scripts live in `src/connectors/run_<name>.py`. When adding a new connector:

### 1. Connector Script Architecture (`src/connectors/run_<name>.py`)

Follow the standard dlt resource pattern:
- **CLI interface**: accept `--client <client_id>` argument.
- **Client configuration**: load tenant parameters from `clients/<client_id>.yml`.
- **Destination dataset**: target `raw_<connector>_<client_id>` with full `replace` write disposition for tenant data isolation.
- **Resilience**:
  - Implement exponential backoff for rate limits (HTTP 429).
  - Implement token expiration detection (HTTP 401) with clean exit or refresh.
  - Apply pagination handling across API result sets.

Example skeleton:

```python
import argparse
import sys
import dlt
from dlt.sources.helpers import requests

def load_client_config(client_id: str) -> dict:
    # Load and parse client YAML configuration
    ...

@dlt.source
def custom_source(client_config: dict):
    @dlt.resource(write_disposition="replace")
    def custom_resource():
        # Fetch data with rate limit and backoff handling
        yield data

def main():
    parser = argparse.ArgumentParser(description="Run Custom Connector")
    parser.add_argument("--client", required=True, help="Client ID")
    args = parser.parse_args()

    config = load_client_config(args.client)
    pipeline = dlt.pipeline(
        pipeline_name=f"custom_{args.client}",
        destination="postgres",
        dataset_name=f"raw_custom_{args.client}",
    )
    load_info = pipeline.run(custom_source(config))
    print(load_info)

if __name__ == "__main__":
    main()
```

### 2. Update Client Template (`clients/_template.yml`)

Add the connector definition block to `clients/_template.yml` with `enabled: false` by default:

```yaml
  custom:
    enabled: false
    account_id: ""
    token_env: CUSTOM_ACCESS_TOKEN_NEW_CLIENT
```

### 3. Update Pipeline Script (`scripts/pipeline.sh`)

Register the connector execution block in `scripts/pipeline.sh` inside the per-client iteration loop:

```bash
if [ "$CUSTOM_ENABLED" = "true" ]; then
    log_info "Running Custom connector for $CLIENT_ID..."
    run_step "$CLIENT_ID" "custom" "python src/connectors/run_custom.py --client $CLIENT_ID"
fi
```

### 4. Create Unit Tests (`tests/test_run_<name>.py`)

Write comprehensive mock-based unit tests covering:
- Successful extraction and pagination.
- Rate-limiting (HTTP 429) backoff.
- Authentication/token expiry (HTTP 401).
- Missing configuration / invalid input fail-fast.

---

## Client YAML configuration

Tenant definitions reside in `clients/*.yml`. Real client configuration files must never be checked into git (`.gitignore` protects `clients/*.yml` except sample files).

Required fields:
- `client_id`: Unique identifier (snake_case or alphanumeric).
- `client_name`: Human-readable name.
- `schema`: Output dbt schema name (`client_<client_id>`).
- `active`: Boolean flag (`true` to process, `false` to skip in scheduled runs).
- `connectors`: Map of supported connectors and credentials.

---

## dbt modeling workflow

Transformations are managed with dbt Core in `src/dbt_project/`:

1. **Source declaration**: Add raw source references in `models/staging/sources.yml`. Jinja dynamically resolves raw schemas per tenant (`raw_<connector>_<client_id>`).
2. **Staging models** (`models/staging/<connector>/stg_<connector>__<entity>.sql`):
   - Standardize column names into snake_case.
   - Cast datatypes explicitly.
   - Inject `client_id` context.
3. **Marts & Intermediate**:
   - Merge cross-platform metrics in intermediate models.
   - Build aggregated marts for reporting (`campaign_performance`, `ad_spend_summary`).
4. **Tenant schema routing**:
   - The macro `generate_schema_name` ensures models resolve to `client_<client_id>`.

---

## Testing & Quality gate

Before opening a pull request, run the complete local quality gate:

```bash
make quality
```

This runs sequentially:
1. `make lint`: `uv run ruff check src/ tests/ main.py`
2. `make typecheck`: `uv run mypy src/ main.py`
3. `make test`: `uv run pytest tests/ -v`

All tests must be deterministic and mock-based. Live network requests during automated tests are strictly prohibited.
