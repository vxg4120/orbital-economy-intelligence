#!/usr/bin/env bash
# Nightly catalog refresh for both apps, run inside the already-running API
# containers. Install via cron on the box (see README "Nightly refresh").
# Politeness-gated by each repo's ingest_run ledger, so extra runs are safe.
set -uo pipefail
cd "$(dirname "$0")"                  # deploy/
set -a; [ -f .env ] && . ./.env; set +a
DC="docker compose"
{
  echo "===== refresh $(date -u +%FT%TZ) ====="
  echo "--- satellite (oei) ---"
  $DC exec -T -e SPACETRACK_IDENTITY="${SPACETRACK_IDENTITY:-}" -e SPACETRACK_PASSWORD="${SPACETRACK_PASSWORD:-}" \
      oei-api python scripts/ingest_all.py || echo "!! oei ingest_all failed"
  $DC exec -T oei-api python scripts/build_graph.py || echo "!! oei build_graph failed"
  $DC exec -T oei-api python quality/report.py      || echo "!! oei report failed"
  echo "--- exodossier (exo) ---"
  $DC exec -T exo-api python scripts/ingest_all.py  || echo "!! exo ingest_all failed"
  $DC exec -T exo-api python scripts/build_graph.py || echo "!! exo build_graph failed"
  $DC exec -T exo-api python quality/report.py      || echo "!! exo report failed"
  echo "===== done $(date -u +%FT%TZ) ====="
} >> ./refresh.log 2>&1
