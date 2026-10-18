# Runbook: Container Unhealthy Remediation

## Overview

This runbook guides operators when Docker containers in the `multitenant-elt` stack fail healthchecks (`agency_postgres`, `agency_pipeline`, or `agency_metabase`).

## Symptoms & Alerts

- Telegram alert: `⚠️ Pipeline Abortado | Container <name> no está healthy`
- Pipeline execution stops before running ETL steps.
- `docker ps` reports `(unhealthy)` status.

## Immediate Triage

1. **Check Healthcheck Failure Output**:
   ```bash
   docker inspect --format='{{json .State.Health}}' <container_name> | jq .
   ```

2. **Check Container Logs**:
   ```bash
   docker logs --tail 100 <container_name>
   ```

3. **Check Host Resources**:
   Verify host disk space and memory availability:
   ```bash
   df -h /
   free -m
   ```

## Root Causes & Remediation

### 1. `agency_postgres` Unhealthy
- **Healthcheck**: `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`.
- **Possible Causes**:
  - Out of disk space on volume `pgdata`.
  - Database crashed or recovery failed.
  - Port 5432 conflict on host.
- **Remediation**:
  ```bash
  # Check postgres logs
  docker logs agency_postgres
  # Restart service
  make docker-db
  ```

### 2. `agency_pipeline` Unhealthy
- **Healthcheck**: Validates python package import, dbt CLI, and TCP reachability to postgres port 5432.
- **Possible Causes**:
  - `agency_postgres` unreachable or network disconnected (`agency_internal_net`).
  - Source bind mounts broken.
- **Remediation**:
  ```bash
  # Verify network existence
  docker network inspect agency_internal_net
  # Restart pipeline container
  make docker-pipeline
  ```

### 3. `agency_metabase` Unhealthy
- **Possible Causes**:
  - PostgreSQL not accepting connections or `metabase_reader` user issue.
  - Java heap out-of-memory.
- **Remediation**:
  ```bash
  docker logs --tail 200 agency_metabase
  make docker-metabase
  ```

## Post-Remediation Verification

Ensure all running containers report `(healthy)`:
```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```
