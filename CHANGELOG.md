# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-11-05

### Breaking Changes

- **Native Python Pipeline Orchestrator**: Migrated end-to-end pipeline orchestration from a 356-line bash script to a native Python engine (`multitenant-elt pipeline` / `src/agency_analytics/pipeline_runner.py`). `scripts/pipeline.sh` has been converted into a slim 30-line host wrapper. Eliminates 40+ container spawns per client, bash YAML parsing, and SQL string interpolation in favor of safe parameterized database records (`%s`), native PyYAML loading, and dynamic plan execution.
- **Sunset Google Ads Developer Token**: Completely removed all references to `developer_token` / `GOOGLE_ADS_DEVELOPER_TOKEN` across code, schemas, and documentation following Google Ads API developer token deprecation. Authorization is now bound directly to Google Cloud projects and OAuth 2.0 consent.
- **Diagnostic Tool Relocation**: Relocated the 713-line standalone CLI probe from `src/agency_analytics/ig_probe.py` to `tools/diagnostics/ig_probe.py` to decouple developer diagnostics from core pipeline runtime imports. A 19-line backward compatibility module is retained at `src/agency_analytics/ig_probe.py`.

### Added

- **Centralized Resilient HTTP Client (`src/agency_analytics/http_client.py`)**: Unified Meta Graph API client with exponential backoff on HTTP 429 and Meta rate limit error codes (4, 17, 80000, 80004, 613), with fast-fail on token expiration (code 190).
- **Unified Google OAuth Renewal Wizard**: Added `multitenant-elt auth google` CLI command and `make auth-google` target to obtain and store permanent OAuth refresh tokens for all Google integrations (Google Ads, GA4, GTM, and YouTube). Supports both global agency credentials (via MCC / manager permissions) and client-isolated credentials (`_CLIENT` suffix).
- **Automated Token Management**: Added `GoogleTokenManager` with automatic caching, proactive refresh before expiry, and automatic token invalidation and refresh on HTTP 401 Unauthorized responses across Google Ads, GTM, and YouTube connectors. Includes `.credentials` property returning official `google.oauth2.credentials.Credentials`.
- **Pipeline Test Suite Expansion**: Expanded automated unit and integration tests to 504 passing tests (505 total, 1 skipped) with global test coverage reaching 94% (all modules $\ge 85\%$).

### Changed

- **Hierarchical Credential Resolution**: Google connectors now look up client-specific OAuth refresh keys first (`GOOGLE_OAUTH_*_{CLIENT}`), then fall back to global agency keys (`GOOGLE_OAUTH_*`), and finally legacy connector-specific variables for backwards compatibility.
- **Dynamic Connector Execution**: Replaced hardcoded bash execution blocks with dynamic connector discovery driven by `pipeline_plan.py`.

## [1.3.0] - 2026-11-04

### Added

- **Meta Graph API Token Renewal Wizard**: Interactive CLI command (`multitenant-elt auth meta` and `make auth-meta`) to inspect short-lived user tokens, exchange them for 60-day long-lived user tokens, discover managed Facebook Pages and linked Instagram Business Accounts, and obtain never-expiring Page Access Tokens.
- **Client Configuration Ingestion**: Optional automated `.env` update utility storing generated tokens for specific clients (`META_ACCESS_TOKEN_{CLIENT}`, `FACEBOOK_ACCESS_TOKEN_{CLIENT}`, `INSTAGRAM_ACCESS_TOKEN_{CLIENT}`).
- **Deterministic Pre-flight Gate**: Added `release-ready` target in `Makefile` combining all code linters (`lint-all`), full unit test suite (`test`), and filesystem vulnerability scan (`test-security`).

### Security

- **Supply Chain Remediation**: Upgraded `certifi` (`2026.10.23`), `gitpython` (`3.2.1`), and `msgpack` (`1.2.4`) in `uv.lock`, mitigating 1 CRITICAL (GHSA-6q48-6c7w-9gw2) and 6 HIGH CVEs (CVE-2026-114145, CVE-2026-114613).
- **Container Base Hardening**: Bumped `python:3.12-slim` base image digest to `b18b261e1b49...` resolving Debian 13.7 CVE-2026-109518 (`apt`/`libapt-pkg7.0`).
- **Environment Topology Hardening**: Pruned redundant `.env.example` files across subdirectories (`services/db/`, `services/metabase/`, `services/pipeline/`), strictly enforcing the root `.env.example` as the canonical configuration Single Source of Truth.

### Fixed

- **CI Enforcement**: Removed path filter on `docker-build-scan-sign.yml` to guarantee the required status check runs on every pull request.
- **Facebook Feed Extraction**: Reduced page post batch fetch limit from 50 to 25 items in `src/connectors/run_facebook.py` to prevent Meta Graph API query complexity 500 errors.

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
