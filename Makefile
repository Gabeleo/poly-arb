.PHONY: install test lint typecheck fmt build run migrate

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check . && ruff format --check .

typecheck:
	mypy polyarb --ignore-missing-imports

fmt:
	ruff format . && ruff check --fix .

build:
	docker compose build

run:
	docker compose up

migrate:
	alembic upgrade head
