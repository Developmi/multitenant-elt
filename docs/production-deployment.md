# Production Deployment & Container Operations Guide

This guide details how to deploy and operate the `multitenant-elt` pipeline image (`ghcr.io/developmi/multitenant-elt:<tag>`) in production environments without requiring development tooling such as `make`.

---

## 1. Container Image Architecture

The production image is published to GitHub Container Registry (GHCR):
- **Image**: `ghcr.io/developmi/multitenant-elt:<tag>`
- **Architecture**: Multi-arch (`linux/amd64`, `linux/arm64`)
- **Security**: Rootless execution (`USER 1000:1000`), no compiler/toolchain in runtime stage, non-root capability drops (`cap_drop: [ALL]`), `no-new-privileges:true`.
- **Integrity**: Signed with Cosign (keyless OIDC), accompanied by CycloneDX SBOM and SLSA Provenance attestations.

---

## 2. Hardened Host Deployment (Clean Host, Zero Repo Clones)

> [!IMPORTANT]
> **Production OS Support:** This deployment model is officially validated and hardened for **Debian 12** and **Ubuntu 22.04 LTS** (aligned with the [Developmi Hardened](https://github.com/Developmi/hardened) L1 OS baseline). Any other distribution (Arch Linux, Fedora, Alpine host, etc.) is considered **experimental**.

For bare-metal and VPS hosts adhering to strict security baselines (such as [Developmi Hardened](https://github.com/Developmi/hardened) with **NIST 800-53** and **CIS Level 1**), running a 24/7 background container for a nightly ELT job is an anti-pattern that wastes resources and complicates compliance.

Instead, use the self-contained bundle in [`deploy/`](../deploy/README.md):
1. **Persistent Database**: Only PostgreSQL 16 Alpine runs continuously via `deploy/compose.prod.yaml` (`docker compose -f compose.prod.yaml up -d`), self-initialized with `deploy/init.sql`.
2. **Mode A (Manual & Ad-hoc Operations)**: Run on-demand pipelines, auth wizards (`auth tiktok`, `auth meta`), or dbt models with `deploy/run.sh` or `docker compose run --rm pipeline`.
3. **Mode B (Automated Ephemeral Execution)**: A host `systemd.timer` wakes up once per night and executes an ephemeral container (`docker run --rm ... ghcr.io/developmi/multitenant-elt:v2.1.0 pipeline`), immediately freeing memory upon completion.
4. **NIST AU-2 / AU-12 Logging**: Output streams natively to `journald` (`journalctl -u multitenant-elt.service`).
5. **No Docker Socket Mounting**: Avoids running third-party schedulers (e.g. Ofelia) that require `/var/run/docker.sock`, preventing CIS Docker Benchmark 5.31 violations.

See the complete guide, CLI wrapper, and systemd templates in [deploy/README.md](../deploy/README.md).

---

## 3. Production Stack Operations (Using `./scripts/prod.sh`)

On target virtual machines or bare metal servers with Docker Engine 24+ and Docker Compose v2 installed, use `./scripts/prod.sh`:

```bash
# 1. Start the core stack (PostgreSQL 16 + Pipeline Worker)
./scripts/prod.sh up

# Or start with Metabase analytics UI enabled:
./scripts/prod.sh up --with-metabase

# 2. Check health and container readiness
./scripts/prod.sh status

# 3. Bootstrap monitoring tables on fresh database
./scripts/prod.sh bootstrap

# 4. Trigger the full ELT pipeline for all active clients
./scripts/prod.sh pipeline

# 5. Tail logs
./scripts/prod.sh logs pipeline

# 6. Stop all services
./scripts/prod.sh down
```

---

## 4. Standalone Container Execution (Kubernetes / AWS ECS / Nomad)

The container ships with an autonomous entrypoint (`/usr/local/bin/docker-entrypoint.sh`).

### A. Run CLI Subcommands
```bash
docker run --rm \
  --env-file .env \
  -v $(pwd)/clients:/app/clients:ro \
  ghcr.io/developmi/multitenant-elt:v2.1.0 \
  pipeline --help
```

### B. Run a Specific Connector
```bash
docker run --rm \
  --network agency_internal_net \
  --env-file .env \
  -v $(pwd)/clients:/app/clients:ro \
  ghcr.io/developmi/multitenant-elt:v2.1.0 \
  connector facebook --client nike
```

### C. Run dbt Build
```bash
docker run --rm \
  --network agency_internal_net \
  --env-file .env \
  ghcr.io/developmi/multitenant-elt:v2.1.0 \
  dbt build --select tag:nike
```

---

## 5. Release & Tagging Process

New production releases are automated via GitHub Actions:
1. Ensure all quality gates pass: `make quality`.
2. Tag a new semantic version (e.g., `git tag v2.1.0`).
3. Push the tag: `git push origin v2.1.0`.
4. The `.github/workflows/docker-build-scan-sign.yml` workflow automatically:
   - Builds `linux/amd64` and `linux/arm64` images.
   - Executes vulnerability gate with Trivy (`--severity CRITICAL,HIGH --exit-code 1`).
   - Generates CycloneDX SBOM.
   - Pushes to GHCR.
   - Signs the image with Cosign keyless signature.
   - Attests SLSA provenance.
