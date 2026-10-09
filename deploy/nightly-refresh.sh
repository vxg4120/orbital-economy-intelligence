#!/usr/bin/env bash
# Nightly catalog refresh for both apps, run inside the already-running API
# containers. Install via cron on the box (see README "Nightly refresh").
# Politeness-gated by each repo's ingest_run ledger, so extra runs are safe.
set -uo pipefail
cd "$(dirname "$0")"                  # deploy/
set -a; [ -f .env ] && . ./.env; set +a
DC="docker compose"

# Every step below runs through `step`, which logs one line when it finishes: how long it took,
# its exit status, and how much the host swapped while it ran. The nightly grew from about 15
# minutes to about 90 by October 2026 on a 4 GB box that leans on swap, with nothing recording
# which step slowed; this is that record. Swap traffic is pswpin + pswpout from /proc/vmstat, in
# 4 KB pages. It logs "n/a" rather than a number whenever the counters cannot be trusted:
# unreadable, not a plain integer, or lower after the step than before it.
swap_pages() {   # pswpin + pswpout as a plain integer, or nothing when unreadable
  # printf, not print: mawk prints large numbers in exponent form, which bash cannot do math on.
  awk '/^pswp(in|out) /{n += $2; seen++} END {if (seen == 2) printf "%.0f\n", n}' /proc/vmstat 2>/dev/null
}
step() {   # step NAME COMMAND...: run COMMAND, then log its time, exit status and swap traffic
  local name=$1 t0 s0 s1 rc swap="n/a"
  shift
  t0=$(date +%s); s0=$(swap_pages)
  "$@"; rc=$?
  s1=$(swap_pages)
  if [[ $s0 =~ ^[0-9]+$ && $s1 =~ ^[0-9]+$ ]] && (( s1 >= s0 )); then
    swap="$(( (s1 - s0) * 4 / 1024 )) MB in+out"
  fi
  echo "--- step $name: $(( $(date +%s) - t0 ))s, exit $rc, swap $swap"
  return "$rc"
}

# Rotate the log before appending: it grows without bound otherwise (~300k lines by Aug 2026),
# and it is the ONLY record of nightly failures, since every step below soft-fails into it.
# One generation is kept (refresh.log.1, overwritten each rotation), so a failure discovered
# late still has up to ~two windows of history. Rotation is size-based and cheap to check.
if [ -f ./refresh.log ] && [ "$(wc -c < ./refresh.log)" -gt 10485760 ]; then   # 10 MB
  mv ./refresh.log ./refresh.log.1
fi

# The line this run starts at, so the alert below reads only this run (0 lines after a rotation).
start_line=$(( $( { wc -l < ./refresh.log; } 2>/dev/null || echo 0) + 1 ))

{
  echo "===== refresh $(date -u +%FT%TZ) ====="
  echo "--- satellite (oei) ---"
  step oei_ingest_all $DC exec -T -e SPACETRACK_IDENTITY="${SPACETRACK_IDENTITY:-}" -e SPACETRACK_PASSWORD="${SPACETRACK_PASSWORD:-}" \
      oei-api python scripts/ingest_all.py || echo "!! oei ingest_all failed"
  step oei_build_graph $DC exec -T oei-api python scripts/build_graph.py || echo "!! oei build_graph failed"
  step oei_refresh_matviews $DC exec -T oei-api python scripts/refresh_matviews.py || echo "!! oei refresh_matviews failed"
  step oei_report $DC exec -T oei-api python quality/report.py      || echo "!! oei report failed"
  step oei_build_bus $DC exec -T oei-api python scripts/build_bus.py   || echo "!! oei build_bus failed"
  step oei_slug_gate $DC exec -T oei-api python scripts/diff_published_buses.py --gate --structural \
                                                    || echo "!! oei SLUG GATE FAILED: a published URL broke"
  # Row-level invariants on what the API now serves (fleet >= on-orbit >= active, percentages,
  # cohort floor, header counters); a failure here means a reader could see an impossible number.
  step oei_assert_published $DC exec -T oei-api python scripts/assert_published.py \
                                                    || echo "!! oei PUBLISH ASSERTIONS FAILED: a served aggregate breaks an invariant"
  step oei_build_rf $DC exec -T oei-api python scripts/build_rf.py    || echo "!! oei build_rf failed"
  step oei_filing_documents $DC exec -T oei-api python scripts/fetch_filing_documents.py --if-stale \
                                                    || echo "!! oei filing documents failed"
  # Runs after the document harvest, since it consumes what that just inventoried.
  step oei_filing_specs $DC exec -T oei-api python scripts/extract_filing_specs.py --if-stale \
                                                    || echo "!! oei schedule S specs failed"
  # Last on purpose: every step above has read tonight's runs. Each ingest lands a full copy of
  # its source, and without this the copies filled the disk to 93% by 2026-09-28. Keeps the
  # newest 3 OK runs per source plus each month's first (docs/specs/raw-retention.md).
  step oei_prune_snapshots $DC exec -T oei-api python scripts/prune_snapshots.py --apply \
                                                    || echo "!! oei prune_snapshots failed"
  echo "--- exodossier (exo) ---"
  step exo_ingest_all $DC exec -T exo-api python scripts/ingest_all.py  || echo "!! exo ingest_all failed"
  step exo_build_graph $DC exec -T exo-api python scripts/build_graph.py || echo "!! exo build_graph failed"
  step exo_report $DC exec -T exo-api python quality/report.py      || echo "!! exo report failed"
  # Last for exo, for the same reason as oei's: exo's raw_* tables land a full copy per pull too,
  # and had reached 6.2 GB of copies by 2026-10-09 (exodossier docs/specs/raw-retention.md).
  step exo_prune_snapshots $DC exec -T exo-api python scripts/prune_snapshots.py --apply \
                                                    || echo "!! exo prune_snapshots failed"
  echo "===== done $(date -u +%FT%TZ) ====="
} >> ./refresh.log 2>&1

# Every failure above is a "!!" line in refresh.log, which nobody reads until something looks
# wrong on the site. If this run printed any, post them as plain text to ALERT_URL (set in
# deploy/.env, never in git); an ntfy.sh topic URL works as is and pushes to a phone. Unset, the
# failures stay in the log and the log says nothing was sent.
failures=$(tail -n +"$start_line" ./refresh.log | grep '^!!' || true)
if [ -n "$failures" ]; then
  if [ -n "${ALERT_URL:-}" ]; then
    if curl -fsS -m 20 -H "Title: vibcreates nightly: $(printf '%s\n' "$failures" | wc -l | tr -d ' ') failure(s)" \
        --data-binary "$failures" "$ALERT_URL" > /dev/null 2>&1; then
      echo "alert sent for this run's failures" >> ./refresh.log
    else
      echo "!! alert could not be sent to ALERT_URL" >> ./refresh.log
    fi
  else
    echo "ALERT_URL is unset, so this run's failures were not sent anywhere" >> ./refresh.log
  fi
fi
