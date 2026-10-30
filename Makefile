# Meridian -- run from WSL (or any shell with make + docker).
# Every target is a thin wrapper around `docker compose`; see README for the
# PowerShell equivalents if you'd rather not use make.

PHASE   ?= phase0
COMPOSE := docker compose --env-file $(if $(wildcard .env),.env,.env.example) -f infra/compose/$(PHASE).yml
PSQL    := $(COMPOSE) exec -T postgres psql -U meridian -d meridian -v ON_ERROR_STOP=1
VENV    ?= $(HOME)/.venvs/meridian

.PHONY: help doctor venv up down reset seed seed-quick psql profile logs ps

help:          ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

doctor:        ## check this machine can run Meridian (read-only)
	@bash scripts/doctor.sh

venv:          ## create $(VENV) with dbt-postgres for Phase 1
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -r requirements-dev.txt
	@$(VENV)/bin/dbt --version
	@echo "activate with: source $(VENV)/bin/activate"

up:            ## start Postgres and wait until it's healthy
	$(COMPOSE) up -d --wait postgres

seed: up       ## full seed: ~5,000 customers, ~10M usage events (~5 min)
	$(COMPOSE) run --rm --build generator

seed-quick: up ## small seed for iterating: 500 customers, 300k events (~15 s)
	$(COMPOSE) run --rm --build generator --quick

profile:       ## row counts and a tour of the data's defects
	$(PSQL) < sql/profile.sql

psql:          ## interactive psql inside the container
	$(COMPOSE) exec postgres psql -U meridian -d meridian

logs:          ## tail Postgres logs
	$(COMPOSE) logs -f postgres

ps:            ## what's running
	$(COMPOSE) ps

down:          ## stop containers, keep the data
	$(COMPOSE) down

reset:         ## stop containers AND delete the database volume
	$(COMPOSE) --profile seed down -v
