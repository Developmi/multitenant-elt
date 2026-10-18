# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-10-17

### Added

- **Production Autonomy & CLI**: Added `multitenant-elt` binary script entrypoint, standalone in-container entrypoint (`scripts/docker-entrypoint.sh`), and dedicated operational orchestrator (`scripts/prod.sh`) without `make` dependency.
- **Cryptographic Developer Tooling**: Implemented `tools/install.sh` and `tools/versions.mk` for SHA256-verified offline binaries (`actionlint`, `hadolint`, `trivy`), with `make tools`, `make doctor`, and local vulnerability gate `make test-security`.
- **Quality & Security Gates**: Added `yamllint` and `zizmor` to `dependency-groups.dev` and unified `make quality` (`lint-all` + `test`).
- **SRE & Production Documentation**: Authored operational runbooks (`pipeline-failure.md`, `container-unhealthy.md`), `DEVELOPMENT.md`, and `docs/production-deployment.md`.
- **Configuration SSOT**: Centralized environment plane in root `.env` with relative pathing across Compose services and eliminated fragmented `.env` sprawl.

### Security

- Dependency upgrades: Updated `urllib3` to `2.8.0` in `uv.lock` resolving CVE-2026-97687 and CVE-2026-97689.
- Replaced non-pinned `pip install` in `run_ga4.py` with `uv add`.
- Hardened `.gitignore` to prevent credential leakage (`.env.*`, `.env.backup*`, `tools/bin/`).

## [1.1.2] - 2026-09-22

### Fixed

- CI: Disable SBOM release asset upload and harden rollback version deletion syntax in `docker-build-scan-sign.yml`.
- Pinned and verified CodeQL upload-sarif action comment references.

## [1.1.1] - 2026-09-19

### Security

- Docker: Pinned `python:3.12-slim` base digest in `Dockerfile` to eliminate upstream Debian CVEs.

## [1.1.0] - 2026-09-07

### Added

- Organic daily marts for Instagram and TikTok profiles, including history tables for insights and follower counts.
- History table archiver for raw-tenant schemas with nullable column hints.
- Pipeline script support for organic marts and dynamic dbt execution planning.

## [1.0.0] - 2026-07-22

### Added

- **10 data connectors** for digital marketing platforms:
  - Ads: Meta Ads, TikTok Ads, Google Ads
  - Organic/Social: Facebook Pages, Instagram Business, TikTok Organic, YouTube Data, Pinterest
  - Analytics: GA4, GTM
- **dbt transformation layer**: 24 staging models, 1 intermediate (`int_unified_spend`), 2 marts (`campaign_performance`, `ad_spend_summary`)
- **Multi-tenant architecture** with per-client schemas and YAML-based client configuration
- **PostgreSQL 16** as the data warehouse
- **Metabase** dashboards with read-only access
- **Pipeline orchestration** via `pipeline.sh` with health checks, Telegram alerts, and per-client loops
- **CLI** (`python main.py`) with `pipeline`, `dlt`, and `dbt` commands
- **92 tests** across all connectors (mock-based, no live API calls)
- **Quality gate**: ruff linting, mypy type checking, pytest
- **Docker Compose** setup for all services (database, pipeline, Metabase)
- **Multi-tenant dbt macros**: `generate_schema_name` and `get_client_sources`
- **`.env.example`** with all required environment variables documented

### Fixed

- All connector tokens read from YAML `token_env` (no hardcoded env vars)
- Google Ads: split `developer_token` (shared) from `access_token` (per-client)
- Volume mount paths corrected for Docker Compose
- dbt `profiles.yml` created for pipeline
- Client YAML files include all 10 connectors (organic ones default to `enabled: false`)
- ISO 8601 duration parsing for YouTube
- GA4 OAuth tokens refreshed per-resource
- Exponential backoff for rate limits across all connectors

### Security

- Postgres bound to `127.0.0.1` only
- Metabase uses read-only database user
- Docker network isolation between services
- `.gitignore` blocks `.env`, `pgdata/`, and all credential-bearing files
