# Standalone Production Deployment Guide

This directory provides the standalone deployment bundle for running `multitenant-elt` in production without cloning the application source code or installing local Python tooling. It uses the pre-built, signed container image from GitHub Container Registry (`ghcr.io/developmi/multitenant-elt:v2.1.0`).

> [!IMPORTANT]
> **Supported Distributions:** This production architecture and its systemd units have been validated and battle-tested exclusively on **Debian 12 (Bookworm)** and **Ubuntu 22.04 LTS (Jammy)**, matching the [Developmi Hardened](https://github.com/Developmi/hardened) Layer 1 OS baseline. Deploying on any other distribution (such as Arch Linux, Fedora, Alpine Linux as host OS, or RHEL) is considered **experimental** and may require manual adjustments to paths, permissions, or systemd sandboxing policies.

---

## 📂 Files Overview & Purpose

| File | Purpose | Why it exists |
| :--- | :--- | :--- |
| `compose.prod.yaml` | Production Docker Compose | Runs **PostgreSQL 16 Alpine** as a persistent service, and defines a `pipeline` service (under `profiles: ["manual"]`) that uses the official GHCR image for ad-hoc executions. |
| `run.sh` | Manual execution CLI wrapper | Frictionless one-command runner for operators to trigger ad-hoc pipelines, auth wizards (`auth tiktok`, `auth meta`), or `dbt` tests in an ephemeral container. |
| `init.sql` | PostgreSQL initial bootstrap script | Mounted into `/docker-entrypoint-initdb.d/01-init.sql`. Automatically executed **only once** on a fresh database to create the pipeline observability tables (`public.pipeline_runs` and `public.pipeline_run_steps`). |
| `systemd/multitenant-elt.service` | Host oneshot execution unit | Triggers the ephemeral container automatically and streams execution logs to `journald`. |
| `systemd/multitenant-elt.timer` | Host scheduler | Wakes up nightly at 03:00 AM with randomized jitter to run the service. |

---

## 🔍 Why `init.sql` is Required

Every time `multitenant-elt pipeline` runs, the internal Python orchestrator (`src/agency_analytics/pipeline_runner.py`) records execution metrics directly in PostgreSQL:
1. `public.pipeline_runs`: Records run status (`running`, `success`, `failed`), timestamps, number of successful/failed connectors, dbt transformation status, and error traces.
2. `public.pipeline_run_steps`: Records granular per-connector progress (e.g., whether Meta Ads succeeded while TikTok failed).
3. **dbt Core**: The internal dbt models (`stg_public__pipeline_runs` and `pipeline_monitoring`) read from these tables to generate monitoring marts.

> **Note:** Without `init.sql`, a freshly launched PostgreSQL container does not have these tables, causing the Python pipeline to fail immediately with `relation "public.pipeline_runs" does not exist`. Mounting `init.sql` ensures the database is self-initializing upon first launch.

---

## 🏗️ Architecture: Ephemeral Oneshot vs. 24/7 Idle Worker

In traditional setups, a worker container is often left running 24/7 with `tail -f /dev/null` waiting for `docker exec` commands. For a batch ELT pipeline that runs once a day:
* **Wastes memory & resources** idling 24/7.
* **Security & Compliance Risk**: Using third-party container schedulers (such as Ofelia) requires mounting `/var/run/docker.sock`, which violates **CIS Docker Benchmark 5.31** and **NIST 800-53 (AC-6 / SC-7)** by giving root-equivalent host access to a container.

### The Hardened Host Pattern
Following the infrastructure baseline from [Developmi Hardened](https://github.com/Developmi/hardened) (NIST 800-53 & CIS Level 1):
1. **Persistent Database**: PostgreSQL runs continuously via `compose.prod.yaml`.
2. **Ephemeral Execution**: Host `systemd.timer` triggers `docker run --rm` nightly.
3. **Automatic Cleanup**: Container terminates and deletes itself (`--rm`) immediately upon completing the pipeline.
4. **Native Auditing**: Every run output and exit code streams to `journald` (`journalctl -u multitenant-elt.service`), fulfilling **NIST 800-53 AU-2 / AU-12**.

---

## 🚀 Step-by-Step Installation

### 1. Prepare Host Directory
```bash
sudo mkdir -p /opt/agency/clients
sudo chown -R $USER:$USER /opt/agency
```

### 2. Copy Deployment Files
Copy `compose.prod.yaml`, `run.sh`, and `init.sql` to `/opt/agency/`:
```bash
cp deploy/compose.prod.yaml /opt/agency/
cp deploy/run.sh /opt/agency/
cp deploy/init.sql /opt/agency/
chmod +x /opt/agency/run.sh
```

### 3. Configure Environment & Clients
```bash
# Create and secure .env (set POSTGRES_PASSWORD, TELEGRAM credentials, etc.)
cp .env.example /opt/agency/.env
chmod 0600 /opt/agency/.env

# Add your client configuration YAMLs
chmod 0600 /opt/agency/clients/*.yml
```

### 4. Start Persistent Database
```bash
cd /opt/agency
docker compose -f compose.prod.yaml up -d

# Verify health status:
docker compose -f compose.prod.yaml ps
```

---

## 🛠️ Usage Mode A: Manual / Ad-hoc Operations

Use `./run.sh` to execute any pipeline command interactively or on-demand. It runs against the production image in an isolated ephemeral container and cleans up after exit:

```bash
# Run full pipeline for all clients:
./run.sh pipeline

# Run pipeline for a specific client:
./run.sh pipeline --client acme

# Run interactive OAuth token renewal (e.g. TikTok Organic or Meta):
./run.sh auth tiktok --client acme
./run.sh auth meta

# Run dbt build or test models:
./run.sh dbt build --select tag:acme
./run.sh dbt test

# View all CLI subcommands:
./run.sh cli --help
```

---

## ⏰ Usage Mode B: Automated Nightly Execution (Systemd Timer)

To schedule automatic nightly runs without third-party schedulers or host pollution:

### 1. Install & Activate Systemd Timer
```bash
# Copy systemd units to host
sudo cp deploy/systemd/multitenant-elt.service /etc/systemd/system/
sudo cp deploy/systemd/multitenant-elt.timer /etc/systemd/system/

# Reload systemd and enable timer
sudo systemctl daemon-reload
sudo systemctl enable --now multitenant-elt.timer

# Verify timer:
systemctl list-timers --all | grep multitenant
```

### 2. Observability & Audit Logs (NIST AU-2 / AU-12)
```bash
# View last 100 lines of execution logs:
journalctl -u multitenant-elt.service -n 100 --no-pager

# Follow live output during an active run:
journalctl -u multitenant-elt.service -f
```
