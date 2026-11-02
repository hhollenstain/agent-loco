.PHONY: sync test lint doctor run watch up-local up-mac up-nvidia pull-model

WORKSPACE ?= examples/demo-project
GOAL ?= Make the test suite pass.
PORT ?= 8080
LBW_POSTGRES_PORT ?= 5432

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
	docker compose -f docker-compose.yml -f docker-compose.postgres.yml build agent
	docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --wait postgres
	trap 'docker compose -f docker-compose.yml -f docker-compose.postgres.yml stop postgres' EXIT INT TERM; \
		LBW_DATABASE_URL=postgresql://lbw:lbw@127.0.0.1:$(LBW_POSTGRES_PORT)/lbw \
		uv run lbw ui --host 127.0.0.1 --port $(PORT) --workspace $(WORKSPACE)

up-mac:
	docker compose -f docker-compose.yml -f docker-compose.mac.yml up --build

up-nvidia:
	docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up --build

pull-model:
	ollama pull $(or $(MODEL),qwen2.5-coder:14b)
