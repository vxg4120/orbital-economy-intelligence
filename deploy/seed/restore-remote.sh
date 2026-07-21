#!/usr/bin/env bash
# Run on the BOX from the deploy/ dir, after `docker compose up -d db` (healthy)
# and after copying oei.dump + exo.dump into deploy/seed/.
# Restores the satellite DB with Timescale pre/post_restore (hypertable + cagg),
# then the vanilla exo DB. Benign "already exists" notices during the oei restore
# are expected — the row-count sanity at the end is the real check.
set -uo pipefail
cd "$(dirname "$0")/.."               # deploy/
DC="docker compose"
D=seed

[ -f "$D/oei.dump" ] || { echo "missing $D/oei.dump"; exit 1; }
[ -f "$D/exo.dump" ] || { echo "missing $D/exo.dump"; exit 1; }

echo "Waiting for db…"; until $DC exec -T db pg_isready -U oei -d oei >/dev/null 2>&1; do sleep 2; done

echo "== Restoring oei (Timescale) =="
$DC cp "$D/oei.dump" db:/tmp/oei.dump
$DC exec -T db psql -U oei -d oei -c "SELECT public.timescaledb_pre_restore();"
$DC exec -T db pg_restore -U oei -d oei --no-owner --no-privileges /tmp/oei.dump || true
$DC exec -T db psql -U oei -d oei -c "SELECT public.timescaledb_post_restore();"
$DC exec -T db rm -f /tmp/oei.dump

echo "== Restoring exo (vanilla) =="
$DC exec -T db psql -U oei -d oei -tc "SELECT 1 FROM pg_database WHERE datname='exo'" | grep -q 1 \
  || $DC exec -T db psql -U oei -d oei -c "CREATE DATABASE exo"
$DC cp "$D/exo.dump" db:/tmp/exo.dump
$DC exec -T db pg_restore -U oei -d exo --no-owner --no-privileges /tmp/exo.dump || true
$DC exec -T db rm -f /tmp/exo.dump

echo "== Sanity =="
$DC exec -T db psql -U oei -d oei -tAc "SELECT 'oei satellites = '||count(*) FROM satellite" || true
$DC exec -T db psql -U oei -d exo -tAc "SELECT 'exo candidates = '||count(*) FROM candidate" || true
echo "OK. Now: docker compose up -d"
