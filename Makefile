.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python3
COMPOSE := docker compose
.PHONY: help install quality test integration up down reset wait demo dbt docs logs quickstart

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Milano Mobility commands:\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create a local virtual environment and install locked development dependencies.
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip==25.0.1
	.venv/bin/pip install -r requirements-dev.lock -e .
	.venv/bin/pip install -r requirements-dbt.lock

quality: ## Run linting, formatting checks, type checks, and unit tests.
	.venv/bin/ruff check ingestion orchestration tests
	.venv/bin/ruff format --check ingestion orchestration tests
	.venv/bin/mypy ingestion
	.venv/bin/pytest -m "not integration" --cov --cov-report=term-missing

test: ## Run unit tests.
	.venv/bin/pytest -m "not integration"

integration: ## Run integration tests against the active Compose stack.
	.venv/bin/pytest -m integration

up: ## Start the complete local platform.
	$(COMPOSE) up -d --build

quickstart: ## Build the core platform and publish the first demo snapshot.
	$(COMPOSE) --profile demo run --build --rm demo

wait: ## Wait until PostgreSQL, MinIO, Airflow, and Metabase are ready.
	./scripts/wait-for-stack.sh

down: ## Stop the local platform without deleting data.
	$(COMPOSE) down

reset: ## Remove local containers and volumes, then rebuild the stack.
	$(COMPOSE) down --volumes --remove-orphans
	$(COMPOSE) up -d --build

demo: ## Download the complete official Milan GTFS feed and build the warehouse.
	$(COMPOSE) --profile demo run --build --rm demo

dbt: ## Build and test all dbt models.
	$(COMPOSE) run --rm --entrypoint dbt pipeline build --project-dir /workspace/transformations/dbt --profiles-dir /workspace/transformations/dbt

docs: ## Generate dbt documentation artifacts.
	$(COMPOSE) run --rm --entrypoint dbt pipeline docs generate --project-dir /workspace/transformations/dbt --profiles-dir /workspace/transformations/dbt

logs: ## Follow service logs.
	$(COMPOSE) logs -f --tail=100
