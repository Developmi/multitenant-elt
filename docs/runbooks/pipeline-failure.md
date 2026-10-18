# Runbook: Pipeline Failure Remediation

## Overview

This runbook guides on-call engineers and operators through diagnosing and remediating failures in `multitenant-elt` execution loops triggered via `scripts/pipeline.sh` or scheduled execution.

## Symptoms & Alerts

- Telegram alert: `❌ Error en Pipeline | Paso: <step> | Cliente: <client>`
- Non-zero exit code in CI or cron task.
- Status `failed` in `public.pipeline_runs` table in PostgreSQL.

## Immediate Triage

1. **Check Pipeline Log**:
   ```bash
   # Host log path (or .pipeline/logs/agency_pipeline.log if unprivileged)
   tail -n 100 /var/log/agency_pipeline.log
   ```

2. **Query Observability Tables**:
   Connect to Postgres and inspect the last failed run and steps:
   ```sql
   SELECT id, client_id, status, error_message, started_at, finished_at
   FROM public.pipeline_runs
   ORDER BY started_at DESC LIMIT 5;

   SELECT step_name, connector, status, error_message
   FROM public.pipeline_run_steps
   WHERE run_id = <RUN_ID>;
   ```

3. **Check Container Status**:
   ```bash
   docker ps --filter "name=agency_"
   ```

## Root Causes & Remediation

### 1. Connector Token Expired / Invalid
- **Symptom**: `401 Unauthorized` or token refresh failure in step log.
- **Remediation**:
  1. Inspect the relevant client configuration in `clients/<client_id>.yml`.
  2. Verify environment variable specified in `token_env`.
  3. Refresh the token via provider portal or helper scripts (e.g., `scripts/youtube_oauth_consent.py`).
  4. Re-run client pipeline:
     ```bash
     uv run python main.py dlt --client <client_id>
     ```

### 2. Rate Limiting / API Quota
- **Symptom**: `429 Too Many Requests` or quota exceeded error.
- **Remediation**:
  - The connectors implement exponential backoff.
  - If quota exceeded (e.g. YouTube 10k units/day), adjust sync window or schedule execution after quota reset.

### 3. dbt Model Failure
- **Symptom**: Step `dbt run` or `dbt test` fails.
- **Remediation**:
  1. Run dbt inside pipeline container with debug output:
     ```bash
     docker exec -it agency_pipeline dbt run --vars '{"client_id": "<client_id>"}'
     ```
  2. Check SQL syntax or schema mismatch in `src/dbt_project/models/`.

## Verification

After remediation, trigger a dry-run or single-client run:
```bash
./scripts/pipeline.sh
```
Verify exit code 0 and Telegram success notification.
