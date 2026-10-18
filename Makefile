# multitenant-elt - Makefile
# uv for deps, Docker Compose for infra
include tools/versions.mk

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

TOOLS := tools/bin

.PHONY: help install lock lint format check typecheck test quality
.PHONY: lint-docker lint-actions lint-all test-security
.PHONY: tools reinstall doctor
.PHONY: clean pristine
.PHONY: setup-networks setup-env pipeline
.PHONY: docker-build docker-build-pipeline
.PHONY: docker-up docker-up-all docker-down docker-logs
.PHONY: docker-db docker-pipeline docker-metabase
.PHONY: db-bootstrap

# === HELP ==================================================================

help:           ## Show this help
	@printf '\033[1mUsage:\033[0m  make \033[36m<target>\033[0m\n\n'
	@awk 'BEGIN {FS = ":.*##"; section=""} \
	  /^# ===/ {section=$$0; gsub(/^# === ?| ?=+$$/,"",section); next} \
	  /^[a-zA-Z_-]+:.*##/ { \
	    if (section != "") {printf "\n  \033[1m%s\033[0m\n", section; section=""} \
	    cmd = $$1; sub(/:.*/,"",cmd); \
	    desc = $$NF; \
	    printf "    \033[36m%-22s\033[0m %s\n", cmd, desc \
	  }' $(MAKEFILE_LIST)

# === DEPENDENCIES ==========================================================

install:        ## Install deps (uv sync)
	uv sync

lock:           ## Lock deps after adding a package
	uv lock && uv sync

# === QUALITY GATE ==========================================================

lint:           ## Lint with ruff
	uv run ruff check src/ tests/ main.py

format:         ## Format with ruff
	uv run ruff format src/ tests/ main.py

check:          ## Check format (read-only)
	uv run ruff format --check src/ tests/ main.py

typecheck:      ## Type-check with mypy
	uv run mypy src/ main.py

test:           ## Run test suite
	uv run pytest tests/ -v

lint-docker:   ## Lint Dockerfile with hadolint
	@test -x $(TOOLS)/hadolint || $(MAKE) tools
	$(TOOLS)/hadolint --failure-threshold warning Dockerfile

lint-actions:  ## Lint and audit GitHub Actions (actionlint + zizmor + yamllint)
	@test -x $(TOOLS)/actionlint || $(MAKE) tools
	$(TOOLS)/actionlint
	uv run zizmor .github/workflows
	uv run yamllint -c .yamllint.yaml .github/workflows .github/dependabot.yml services/

lint-all:      ## Run all linters (python, docker, actions, yaml)
	$(MAKE) lint
	$(MAKE) check
	$(MAKE) typecheck
	$(MAKE) lint-docker
	$(MAKE) lint-actions

test-security: ## Run Trivy filesystem vulnerability scan
	@test -x $(TOOLS)/trivy || $(MAKE) tools
	$(TOOLS)/trivy fs --scanners vuln --severity CRITICAL,HIGH --ignore-unfixed .

quality:       ## Full gate: lint-all -> test -> test-security
	$(MAKE) lint-all
	$(MAKE) test

# === DEV TOOLS =============================================================

tools:          ## Install pinned development tools (actionlint, hadolint, trivy)
	@ACTIONLINT_VERSION=$(ACTIONLINT_VERSION) \
	ACTIONLINT_SHA256_LINUX_AMD64=$(ACTIONLINT_SHA256_LINUX_AMD64) \
	ACTIONLINT_SHA256_LINUX_ARM64=$(ACTIONLINT_SHA256_LINUX_ARM64) \
	ACTIONLINT_SHA256_DARWIN_AMD64=$(ACTIONLINT_SHA256_DARWIN_AMD64) \
	ACTIONLINT_SHA256_DARWIN_ARM64=$(ACTIONLINT_SHA256_DARWIN_ARM64) \
	HADOLINT_VERSION=$(HADOLINT_VERSION) \
	HADOLINT_SHA256_LINUX_AMD64=$(HADOLINT_SHA256_LINUX_AMD64) \
	HADOLINT_SHA256_LINUX_ARM64=$(HADOLINT_SHA256_LINUX_ARM64) \
	HADOLINT_SHA256_DARWIN_AMD64=$(HADOLINT_SHA256_DARWIN_AMD64) \
	HADOLINT_SHA256_DARWIN_ARM64=$(HADOLINT_SHA256_DARWIN_ARM64) \
	TRIVY_VERSION=$(TRIVY_VERSION) \
	TRIVY_SHA256_LINUX_AMD64=$(TRIVY_SHA256_LINUX_AMD64) \
	TRIVY_SHA256_LINUX_ARM64=$(TRIVY_SHA256_LINUX_ARM64) \
	TRIVY_SHA256_DARWIN_AMD64=$(TRIVY_SHA256_DARWIN_AMD64) \
	TRIVY_SHA256_DARWIN_ARM64=$(TRIVY_SHA256_DARWIN_ARM64) \
	./tools/install.sh

reinstall:      ## Force reinstall development tools
	@REINSTALL=1 $(MAKE) tools

doctor:         ## Check development environment and tool versions
	@test -x $(TOOLS)/actionlint || $(MAKE) tools
	@printf "\n"
	@printf "\033[1mDevelopment Environment\033[0m\n"
	@printf "=======================\n\n"
	@printf "%-18s %s\n" "Python" "$$(uv run python --version | awk '{print $$2}')"
	@printf "%-18s %s\n" "uv" "$$(uv --version | awk '{print $$2}')"
	@printf "%-18s %s\n" "Docker" "$$(docker version --format '{{.Client.Version}}' 2>/dev/null || echo 'not running')"
	@printf "%-18s %s\n" "actionlint" "$$($(TOOLS)/actionlint -version 2>/dev/null | head -1)"
	@printf "%-18s %s\n" "hadolint" "$$($(TOOLS)/hadolint --version 2>/dev/null | awk '{print $$NF}')"
	@printf "%-18s %s\n" "trivy" "$$($(TOOLS)/trivy --version 2>/dev/null | head -1 | awk '{print $$2}' || echo 'not installed')"
	@printf "%-18s %s\n" "yamllint" "$$(uv run yamllint --version | awk '{print $$2}')"
	@printf "%-18s %s\n" "zizmor" "$$(uv run zizmor --version | awk '{print $$2}')"
	@printf "\n"

# === CLEANUP ===============================================================

clean:          ## Remove Python artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete

pristine: clean ## Remove everything + .venv
	rm -rf .venv

# === LOCAL SETUP ===========================================================

setup-networks: ## Create Docker networks
	./scripts/setup-networks.sh

setup-env:      ## Copy global .env.example -> .env
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env"; else echo ".env already exists."; fi

pipeline:       ## Run pipeline orchestration
	./scripts/pipeline.sh

# === DOCKER - BUILD ========================================================

docker-build:           ## Build all images (OCI labels: REVISION=local + CREATED now)
	docker build --build-arg REVISION=local --build-arg CREATED=$$(date -u +%Y-%m-%dT%H:%M:%SZ) -t agency-pipeline:latest .

docker-build-nocache:   ## Cold build (no layer cache)
	docker build --no-cache --build-arg REVISION=local --build-arg CREATED=$$(date -u +%Y-%m-%dT%H:%M:%SZ) -t agency-pipeline:latest .

# === DOCKER - STACKS =======================================================

docker-up:      ## Start Postgres + Pipeline
	$(MAKE) docker-db
	sleep 3
	$(MAKE) docker-pipeline

docker-up-all:  ## Start Postgres + Pipeline + Metabase (opt-in profile)
	$(MAKE) docker-db
	sleep 3
	$(MAKE) docker-pipeline
	$(MAKE) docker-metabase

docker-down:    ## Stop all services
	-docker compose -f services/db/compose.yaml down 2>/dev/null
	-docker compose -f services/pipeline/compose.yaml down 2>/dev/null
	-docker compose --profile metabase -f services/db/compose.yaml -f services/metabase/compose.yaml down 2>/dev/null

docker-logs:    ## Tail logs from all services
	@docker compose -f services/db/compose.yaml logs -f &
	@docker compose -f services/pipeline/compose.yaml logs -f &
	@docker compose --profile metabase -f services/db/compose.yaml -f services/metabase/compose.yaml logs -f metabase &
	@wait

# === DOCKER - SERVICES =====================================================

docker-db:      ## Start Postgres 16
	docker compose -f services/db/compose.yaml up -d

docker-pipeline: ## Start pipeline worker
	docker compose -f services/pipeline/compose.yaml up -d

docker-metabase: ## Start Metabase (opt-in profile)
	docker compose --profile metabase -f services/db/compose.yaml -f services/metabase/compose.yaml up -d metabase

# === BOOTSTRAP ==============================================================

db-bootstrap:   ## Bootstrap dbt monitoring chain on a clean DB (requires: make docker-up)
	@echo "==> db-bootstrap: esperando agency_postgres y agency_pipeline healthy..."
	@for c in agency_postgres agency_pipeline; do
		ok=""
		for i in $$(seq 1 60); do
			s="$$(docker inspect --format='{{.State.Health.Status}}' "$$c" 2>/dev/null || echo missing)"
			if [ "$$s" = "healthy" ]; then ok=1; break; fi
			sleep 2
		done
		if [ -z "$$ok" ]; then echo "ERROR: $$c no está healthy ($$s)"; exit 1; fi
		echo "    $$c: healthy"
	done
	@chain="$$(docker exec -w /app/src agency_pipeline python3 -c 'from agency_analytics.pipeline_plan import MONITORING_CHAIN; print(" ".join(MONITORING_CHAIN))')" || exit 1
	@if [ -z "$$chain" ]; then echo "ERROR: cadena de monitoreo vacía (módulo pipeline_plan)"; exit 1; fi
	@echo "==> db-bootstrap: dbt build de la cadena de monitoreo (sin datos externos): $$chain"
	@docker exec -w /app/src/dbt_project agency_pipeline \
		dbt build --select $$chain --profiles-dir . \
		|| { echo "ERROR: dbt build de la cadena falló"; exit 1; }
	@echo "==> db-bootstrap: OK — cadena de monitoreo creada (post-wipe, sin datos externos)."
	@echo "    El grafo completo (conectores) requiere cargas dlt previas: las vistas"
	@echo "    staging sobre schemas raw_* vacíos fallan. Corré 'make pipeline' (o el"
	@echo "    pipeline nocturno) para poblar raw_* con dlt antes del build completo."
