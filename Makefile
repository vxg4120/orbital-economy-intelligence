.PHONY: venv up down psql migrate metrics report test lint api web-dev web-build fe

venv:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

up:
	docker compose up -d
	@echo "Waiting for oei-db to be healthy..."
	@until [ "$$(docker inspect --format '{{.State.Health.Status}}' oei-db 2>/dev/null)" = "healthy" ]; do sleep 1; done
	@echo "oei-db is healthy."

down:
	docker compose down

psql:
	docker exec -it oei-db psql -U oei -d oei

migrate:
	.venv/bin/python scripts/migrate.py

metrics:
	.venv/bin/python scripts/apply_metrics.py

report:
	.venv/bin/python quality/report.py

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check .

# --- Orbital Economy Terminal (frontend) ---------------------------------
api:
	.venv/bin/uvicorn api.main:app --port 8600 --reload

web-dev:
	pnpm -C web dev

web-build:
	pnpm -C web build

# Build the SPA, then serve it (and the API) from a single process at :8600.
fe: web-build api
