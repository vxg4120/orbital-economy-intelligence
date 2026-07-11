.PHONY: venv up down psql migrate metrics report test lint api web-dev web-build fe gold-queue review gold-score

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

# --- Gold evaluation set (identity-resolution ground truth) --------------
# (re)select hard cases into gold_case; idempotent, never overwrites verdicts.
gold-queue:
	.venv/bin/python scripts/build_gold_queue.py

# arbitrate unlabeled cases interactively (resumable). Pass ARGS='--type owner_dispute --limit 20'.
review:
	.venv/bin/python scripts/review.py $(ARGS)

# write docs/reports/gold_eval.md from verdicts so far (graceful at 0 labeled).
gold-score:
	.venv/bin/python scripts/score_gold.py

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

# Expose the local terminal on the public web via a free Cloudflare quick tunnel.
# Prints a random *.trycloudflare.com URL; the API must already be running (make fe).
# URL is ephemeral: it changes on every run and dies with the process / laptop sleep.
share:
	cloudflared tunnel --url http://localhost:8600

# Install/remove the twice-daily ingest launchd job (user-level; your call to activate).
schedule-install:
	cp ops/com.oei.daily-ingest.plist ~/Library/LaunchAgents/
	launchctl load ~/Library/LaunchAgents/com.oei.daily-ingest.plist
	@echo "installed: twice-daily ingest at 07:10 and 19:10 local"
schedule-remove:
	launchctl unload ~/Library/LaunchAgents/com.oei.daily-ingest.plist || true
	rm -f ~/Library/LaunchAgents/com.oei.daily-ingest.plist
