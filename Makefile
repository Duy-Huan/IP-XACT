# Industrial developer & CI entry point for socsim-platform (IP-XACT RISC-V Virtual Platform).
# Run `make help` for available targets.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

VENV ?= .venv
PYTHON := $(VENV)/bin/python
USP := $(VENV)/bin/usp
JOBS ?= $(shell nproc 2>/dev/null || echo 2)

.PHONY: help venv venv-pip setup-check setup-qemu setup-gem5 setup-chipyard setup-opentitan setup-all \
        lint format typecheck ci-lint docs-check audit test check \
        podman-build podman-smoke podman-qa podman-mcp podman-run clean

help: ## Show available industrial workflow targets
	@awk 'BEGIN {FS = ":.*## "; printf "\nUsage: make <target> [VENV=.venv] [JOBS=N]\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create/sync Python .venv using uv (all extras) or pip fallback
	@bash scripts/setup_venv.sh --extra all --venv "$(VENV)"

venv-pip: ## Create/sync Python .venv using standard python3 -m venv + requirements-all.txt
	@bash scripts/setup_venv.sh --method pip --extra all --venv "$(VENV)"

setup-check: ## Non-destructive audit of Python venv + QEMU/gem5/Chipyard/OpenTitan toolchains
	@bash scripts/setup_all.sh --check --venv "$(VENV)"

setup-qemu: ## Fetch & check pinned QEMU v10.0.0 source (add BUILD=1 to compile)
	@if [ "$${BUILD:-0}" = "1" ]; then bash scripts/setup_qemu.sh --build --jobs "$(JOBS)"; else bash scripts/setup_qemu.sh --fetch-only; fi

setup-gem5: ## Fetch & check pinned gem5 v24.1.0.3 source (add BUILD=1 to compile)
	@if [ "$${BUILD:-0}" = "1" ]; then bash scripts/setup_gem5.sh --build --jobs "$(JOBS)"; else bash scripts/setup_gem5.sh --fetch-only; fi

setup-chipyard: ## Fetch pinned Chipyard 1.13.0 source & SHA-256 verified sbt/firtool
	@bash scripts/setup_chipyard.sh --fetch-only --tools

setup-opentitan: ## Fetch & verify pinned OpenTitan earlgrey_silver_release_v5 RTL
	@bash scripts/setup_opentitan.sh --fetch-only --verify-rtl

setup-all: ## Orchestrate venv + all pinned upstream platform checkouts (no heavy builds unless BUILD=1)
	@if [ "$${BUILD:-0}" = "1" ]; then bash scripts/setup_all.sh --all --build --jobs "$(JOBS)" --venv "$(VENV)"; else bash scripts/setup_all.sh --all --fetch-only --venv "$(VENV)"; fi

lint: ## Run Ruff lint and format checks
	@"$(VENV)/bin/ruff" format --check .
	@"$(VENV)/bin/ruff" check .

format: ## Auto-format Python codebase with Ruff
	@"$(VENV)/bin/ruff" format .
	@"$(VENV)/bin/ruff" check --fix .

typecheck: ## Run mypy static type checker on src/socsim
	@"$(VENV)/bin/mypy" --ignore-missing-imports --check-untyped-defs src/socsim

ci-lint: ## Validate GitHub Actions workflows (actionlint) and all tracked shell scripts
	@bash scripts/ci_lint.sh

docs-check: ## Verify required industrial documentation and example guides
	@"$(PYTHON)" scripts/check_docs.py

audit: ## Validate M1-M9 upgrade ledger and readiness audit
	@"$(USP)" upgrade --root . --audit

test: ## Run full pytest regression suite
	@"$(PYTHON)" -m pytest -q

check: lint typecheck ci-lint docs-check audit test ## Run full local quality gate (lint + typecheck + ci-lint + docs + audit + pytest)

podman-build: ## Build rootless Podman SDK OCI image from committed HEAD
	@bash scripts/podman.sh build

podman-smoke: ## Run offline Podman SDK native smoke + Verilator RTL + MCP verification
	@bash scripts/podman.sh smoke

podman-qa: ## Run full QA/QC suite inside rootless Podman SDK (SUITE=full)
	@bash scripts/podman.sh qa "$${SUITE:-full}"

podman-mcp: ## Start MCP stdio server inside rootless Podman SDK
	@bash scripts/podman.sh mcp

podman-run: ## Launch interactive shell inside rootless Podman SDK
	@bash scripts/podman.sh run

clean: ## Remove ephemeral local caches (preserves .venv, .tools, .podman, results)
	@rm -rf .pytest_cache .mypy_cache .ruff_cache
	@find src tests -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
