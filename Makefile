.PHONY: sync test lint doctor run watch up-local restart-local up-mac up-nvidia pull-model

WORKSPACE ?= examples/demo-project
GOAL ?= Make the test suite pass.
PORT ?= 8080
LBW_POSTGRES_PORT ?= 5432
LBW_KIWIFS_PORT ?= 3333
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.postgres.yml -f docker-compose.mac.yml
COMPOSE_ENV := LBW_UI_PORT=$(PORT) LBW_POSTGRES_PORT=$(LBW_POSTGRES_PORT) LBW_KIWIFS_PORT=$(LBW_KIWIFS_PORT)

sync:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff format src tests
	uv run ruff check src tests

doctor:
	uv run lbw doctor

run:
	uv run lbw run --workspace $(WORKSPACE) --goal "$(GOAL)"

watch:
	uv run lbw watch --workspace $(WORKSPACE)

up-local:
	$(COMPOSE_ENV) $(COMPOSE) build agent
	$(COMPOSE_ENV) $(COMPOSE) up -d --wait postgres
	$(COMPOSE_ENV) $(COMPOSE) up -d kiwifs
	$(COMPOSE_ENV) $(COMPOSE) up -d --no-deps agent

restart-local:
	$(COMPOSE_ENV) $(COMPOSE) up -d --build --no-deps --force-recreate agent

up-mac:
	docker compose -f docker-compose.yml -f docker-compose.mac.yml up --build

up-nvidia:
	docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up --build

pull-model:
	ollama pull $(or $(MODEL),qwen2.5-coder:14b)
