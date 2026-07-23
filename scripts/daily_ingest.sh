#!/bin/bash
# Daily ingestion cycle: polite pulls -> identity rebuild -> DQ report.
# Intended to run via launchd (see ops/com.oei.daily-ingest.plist) or manually.
# All pulls remain politeness-gated by the ingest_run ledger, so running this
# more often than the source intervals allow is safe (skipped_fresh).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then set -a; source .env; set +a; fi

LOG=data/daily_ingest.log
{
  echo "=== daily ingest $(date -u +%FT%TZ) ==="
  .venv/bin/python scripts/ingest_all.py
  .venv/bin/python scripts/build_graph.py
  # Bus Benchmarks: attribution rebuild + behavior matview refresh + idempotent monthly
  # leaderboard snapshot (the first run of each month freezes it; later runs insert nothing).
  .venv/bin/python scripts/build_bus.py
  .venv/bin/python quality/report.py
  # Rollover watch: celebrate the first 6-digit catalog number when it lands.
  .venv/bin/python - <<'EOF'
from common.db import get_conn
cur = get_conn().cursor()
cur.execute("SELECT max(norad_cat_id) FROM raw_satcat")
mx = cur.fetchone()[0]
print(f"[rollover-watch] max NORAD = {mx}")
if mx > 99999:
    print("[rollover-watch] *** 6-DIGIT CATALOG NUMBERS ARE HERE — commit this moment ***")
EOF
  echo "=== done $(date -u +%FT%TZ) ==="
} >> "$LOG" 2>&1
