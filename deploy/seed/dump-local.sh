#!/usr/bin/env bash
# Run on the LAPTOP. Dumps the live oei + exo databases to deploy/seed/*.dump
# (custom format) for transfer to the box. Requires the local oei-db container up.
set -euo pipefail
cd "$(dirname "$0")"                 # deploy/seed/
SRC=${SRC_CONTAINER:-oei-db}

echo "Dumping oei (custom format)…"
docker exec "$SRC" pg_dump -U oei -Fc -d oei -f /tmp/oei.dump
docker cp "$SRC:/tmp/oei.dump" ./oei.dump
docker exec "$SRC" rm -f /tmp/oei.dump

echo "Dumping exo (custom format)…"
docker exec "$SRC" pg_dump -U oei -Fc -d exo -f /tmp/exo.dump
docker cp "$SRC:/tmp/exo.dump" ./exo.dump
docker exec "$SRC" rm -f /tmp/exo.dump

ls -lh ./*.dump
echo
echo "Next: copy these to the box, e.g."
echo "  scp oei.dump exo.dump <user>@<box-ip>:~/apps/space/deploy/seed/"
echo "then on the box run:  ./seed/restore-remote.sh"
