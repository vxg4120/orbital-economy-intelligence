# Orbital deploy — one small box, two stable apps

Runs both portfolio apps on a single always-on Hetzner box with real HTTPS domains,
replacing the laptop + ephemeral Cloudflare quick-tunnels (which flap whenever the
laptop's network drops).

```
                        Internet
                           │  (A records: orbital.DOMAIN, exo.DOMAIN)
                     ┌─────▼─────┐   ports 80/443
                     │   Caddy   │   automatic Let's Encrypt TLS
                     └──┬─────┬──┘
             orbital ───┘     └─── exo
              ┌──────────┐   ┌──────────┐
              │ oei-api  │   │ exo-api  │   FastAPI + built React SPA
              │  :8600   │   │  :8700   │
              └────┬─────┘   └────┬─────┘
                   └──────┬───────┘
                     ┌────▼────┐
                     │   db    │   TimescaleDB 2.28.2-pg17
                     │ oei+exo │   (single cluster, two databases)
                     └─────────┘
```

**Cost:** Hetzner CX33 (x86, 4 vCPU / 8 GB / 80 GB, $9.99/mo plus $0.60 for the IPv4, billed in USD before VAT; resized from CX23 at $6.49 on 2026-10-12 with `hcloud server change-type orbital cx33`, about a minute of downtime) · domain ≈ $10–12/yr.
Everything else (Caddy, Let's Encrypt, Docker) is free.

**Why one box:** the satellite app *requires* TimescaleDB at query time (a continuous
aggregate + compressed hypertable) and the data is ~3.9 GB — past every free managed
tier. Self-hosting the Timescale image on one VPS is both the cheapest and the simplest
stable option; splitting across serverless services wouldn't remove that DB cost.

---

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | db + oei-api + exo-api + caddy |
| `caddy/Caddyfile` | reverse proxy + auto-HTTPS for the landing page and both subdomains |
| `.env.example` | copy to `.env`, fill in secrets (gitignored) |
| `bootstrap.sh` | one-time box setup (Docker, clone repos) |
| `seed/dump-local.sh` | **on laptop** — dump oei + exo to `seed/*.dump` |
| `seed/restore-remote.sh` | **on box** — restore the dumps into the cluster |
| `nightly-refresh.sh` | cron job: refresh both catalogs nightly |
| `landing-snapshot.sh` | **before a landing deploy**: re-bake the landing's cached numbers from `/live/*`, then commit |

The two app images are built from `../Dockerfile` (space) and `../../exodossier/Dockerfile`,
so **both repos must be cloned as siblings** (`bootstrap.sh` does this):

```
~/apps/
  space/        deploy/   ← run compose from here
  exodossier/
```

---

## One-time rollout

### 1. Buy a domain (~$10–12/yr)
Any registrar (Porkbun, Cloudflare Registrar, and Namecheap are all cheap). A `.space`
TLD is thematic; `.dev`/`.app` are fine too. You need one domain — the apps live on
subdomains `orbital.` and `exo.`.

### 2. Create the Hetzner box
Hetzner Cloud → new project → **Add Server**:
- Location: pick one near you (e.g. Ashburn/Hillsboro for US, Falkenstein for EU).
- Image: **Ubuntu 24.04**.
- Type: **Arm64 → CAX11** (2 vCPU / 4 GB / 40 GB, ≈ €4.5/mo).
- SSH key: add yours (`cat ~/.ssh/id_ed25519.pub`; create with `ssh-keygen -t ed25519` if needed).
- Create. Note the public **IPv4**.

### 3. Point DNS
At your registrar (or Cloudflare DNS), add two **A records** → the box IPv4:

```
orbital.<yourdomain>   A   <box-ip>
exo.<yourdomain>       A   <box-ip>
```

If you use Cloudflare, set these two records to **DNS only (grey cloud)** for the first
boot so Caddy can complete the Let's Encrypt HTTP challenge; you can enable the proxy
(orange cloud) later.

### 4. Bootstrap the box
```bash
ssh root@<box-ip>
curl -fsSL https://raw.githubusercontent.com/vxg4120/orbital-economy-intelligence/main/deploy/bootstrap.sh | bash
# then edit secrets:
nano ~/apps/space/deploy/.env      # POSTGRES_PASSWORD, BASE_DOMAIN, ACME_EMAIL, (creds)
```
Generate a strong DB password with `openssl rand -hex 24`.

### 5. Seed the databases
On the **laptop** (with the local `oei-db` running):
```bash
cd ~/Development/repos/space/deploy
./seed/dump-local.sh
scp seed/oei.dump seed/exo.dump root@<box-ip>:~/apps/space/deploy/seed/
```
On the **box**:
```bash
cd ~/apps/space/deploy
docker compose up -d --build db      # start just the DB
./seed/restore-remote.sh             # restores oei (Timescale) + exo
```

### 6. Go live
```bash
docker compose up -d --build         # apis + caddy; TLS provisions in ~30s
```
Verify:
```bash
curl -s https://orbital.<domain>/api/stats | head
curl -s https://exo.<domain>/api/health
```
Open both in a browser — the SPAs load over real HTTPS.

### 7. Nightly refresh (cron)
```bash
crontab -e
# refresh both catalogs at 07:10 and 19:10 (matches the old cadence; trim to once if you like)
10 7,19 * * * /root/apps/space/deploy/nightly-refresh.sh
```
Logs land in `deploy/refresh.log`. Add `SPACETRACK_IDENTITY` / `SPACETRACK_PASSWORD` to
`.env` if you want the deep satellite history to keep advancing; without them the nightly
CelesTrak pull (current elements) still runs.

**Space-Track step (`oei_spacetrack`).** Between `oei_ingest_all` and `oei_build_graph`, the
nightly runs `scripts/spacetrack_topup.py`, which does two things at most once a day each:
it asks Space-Track's `gp_history` class for the element sets *created* since the last
successful night (the `spacetrack_cursor` table holds that mark; windows never overlap,
because Space-Track allows each element set to be downloaded once) for the benchmark fleet,
about 126 requests of 100 NORAD ids, and lands them in `gp_elements` as
`spacetrack_gp_history`; then it pulls the last 30 days of actual decays (`decay` class,
`MSG_TYPE` Historical) into `raw_spacetrack_decay`, read through `v_spacetrack_decay_actual`.
Requests are spaced 3 s apart and capped at 250 an hour, under Space-Track's 30 a minute and
300 an hour. With blank credentials the step prints one line and exits 0. `deploy/` is
bind-mounted but `scripts/` and `ingest/` are baked into the image, so the step needs
`docker compose up -d --build oei-api` and migration 0027 before it can run; until then it
soft-fails as `!! oei spacetrack top-up failed`. Spec: `docs/specs/spacetrack-topup.md`.

### 8. Retire the laptop demo
Once the box is verified, stop the laptop's tunnels/watchdog and repoint any shared links
to the new domains. The GitHub repos stay the durable code home.

---

## Operations

**Update after a git push:**
```bash
cd ~/apps/space && git pull
cd ~/apps/exodossier && git pull
cd ~/apps/space/deploy && docker compose up -d --build
```

**Logs:** `docker compose logs -f oei-api` (or `exo-api`, `caddy`, `db`).

### Disk and snapshot retention

Every ingest run lands a full copy of its source in the `raw_*` tables. The nightly's last oei
step, `scripts/prune_snapshots.py --apply`, keeps the newest 3 OK runs per table plus the first
run of each month and deletes the other finished runs, so the tables stop growing. The policy
and its reasons are in [docs/specs/raw-retention.md](../docs/specs/raw-retention.md). The
per-run assertion ledger, `source_assertion`, grew the same way (about 88 MB a day); it was
replaced by the `claim` table (one row per claim with its run range, 161 MB, see
[docs/specs/assertion-model.md](../docs/specs/assertion-model.md)) and renamed
`source_assertion_runs_20261012` on 2026-10-12. Drop it after 2026-10-19, outside the windows,
then `VACUUM`:

```bash
docker compose exec -T db psql -U oei -d oei -c 'DROP TABLE source_assertion_runs_20261012'
df -h /
```

#### Rolling the ledger back (before the drop)

The renamed table is frozen at the rename and has no foreign key to `satellite`, so a week
of merges may leave rows pointing at deleted shells, and it holds no run after the rename.
Rolling back is therefore a procedure (rehearsed 2026-10-12 on a local copy), outside the
windows:

```bash
cd /root/apps/space && git revert --no-edit <the retirement merge> && cd deploy
docker compose up -d --build oei-api        # the dual writer and claim_is_current readers
docker compose exec -T db psql -U oei -d oei <<'SQL'
ALTER TABLE source_assertion_runs_20261012 RENAME TO source_assertion;
UPDATE source_assertion a SET satellite_id = NULL
 WHERE satellite_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM satellite s WHERE s.satellite_id = a.satellite_id);
ALTER TABLE source_assertion ADD CONSTRAINT source_assertion_satellite_id_fkey
  FOREIGN KEY (satellite_id) REFERENCES satellite;
DELETE FROM schema_migrations WHERE filename = '0024_ledger_retired.sql';
SQL
# then recreate claim_is_current from db/migrations/0022_linked_assertions.sql (the function
# body, unchanged) and run the next nightly: extract() fills the newest run idempotently.
```

The `exo` database lands copies the same way, and the nightly's last exo step runs exo's own
`scripts/prune_snapshots.py --apply` with the same policy (since 2026-10-09). Its one-time
compaction follows the ordered Rollout in the exodossier repo's `docs/specs/raw-retention.md`:
a verified laptop backup first, then `--compact` under `nohup` with a log, and the nightly step
only after that. The dry-run command below works for exo with `exo-api` in place of `oei-api`.
Exo's `source_assertion` is rebuilt from the newest runs every night, so it does not grow.

Deleting rows does not shrink the files. To return the space of a backlog to the OS, run the
one-time rewrite, outside the 07:10 and 19:10 UTC windows. Each table is locked while it is
rewritten, for seconds per table:

```bash
df -h /                                                                  # before
docker compose exec -T oei-api python scripts/prune_snapshots.py            # dry run: the plan
docker compose exec -T oei-api python scripts/prune_snapshots.py --compact  # rewrite
df -h /                                                                  # after
```

`--compact` only rewrites tables that still have droppable runs. If the nightly's `--apply`
has already deleted the backlog (it happened on 2026-10-01: 64 minutes of DELETE and VACUUM),
the dead space is inside the files and `--compact` reports nothing to drop. Then run
`VACUUM (FULL, ANALYZE) <table>` per table, largest first, under `nohup` with a log so a
dropped SSH session cannot interrupt it; each needs only its kept size as headroom and frees
its old file when it finishes (2026-10-01: all 13 tables in 35 s, 2.6 GB to 8.1 GB free).

Never run an ad-hoc `count(DISTINCT ...)` or a large sort against
`source_assertion_runs_20261012` (while it exists) or any other multi-GB table on the box:
Postgres spills the sort to disk, and on 2026-09-29 one such query filled the disk.

### Caddy configuration updates

Run these operator steps from `space/deploy`, outside the 07:10 and 19:10 UTC
nightly ingest windows. The `caddy/` directory is mounted read-only, rather than
the Caddyfile itself: replacing a single bound file through git or an editor can
leave the container reading its old inode even after a graceful reload.
See the [official image guidance](https://github.com/docker-library/docs/blob/master/caddy/README.md#-do-not-mount-the-caddyfile-directly-at-etccaddycaddyfile).

When first adopting the directory mount, recreate **only Caddy** so Docker picks
up the new mount (brief proxy interruption; existing TLS/config volumes persist):

```bash
docker compose up -d --no-deps --force-recreate caddy
```

For subsequent configuration updates, run this checked sequence; a successful
API image rebuild does not reload Caddy. The hash comparison also detects an old
single-file mount. Do not hide command failures behind unchecked `tail` pipelines.

```bash
(
  set -eu
  host_hash=$(sha256sum caddy/Caddyfile)
  mounted_hash=$(docker compose exec -T caddy sha256sum /etc/caddy/Caddyfile)
  test "${host_hash%% *}" = "${mounted_hash%% *}"
  docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  python3 ../scripts/check_landing.py https://vibcreates.com
)
```

The read-only smoke check requires an unknown URL to return both HTTP 404 and the
styled recovery page. HTTP status alone, a direct `/404.html` request, or Caddyfile
validation does not establish that visitors receive the recovery page.

### Landing snapshot numbers

`landing/index.html` carries hardcoded copies of the numbers its own script fetches from
`/live/orbital` and `/live/exo`. They are what the first paint, scrapers and a no-JS reader
see, and what a tile keeps when its platform is down. Nothing refreshes them automatically,
so re-bake them before any landing deploy and commit the result, from the repository root:

```bash
deploy/landing-snapshot.sh            # against https://$BASE_DOMAIN, or https://vibcreates.com
git add deploy/landing/index.html && git commit -m "Landing: snapshot $(date -u +%F)"
```

It bakes whatever the live endpoints serve at that moment, so a deploy that itself changes
the data (a graph rebuild, a dedupe) ships numbers from before the change. When that
happens, run it once more after the deploy has settled and pull again.

**Backups:** Hetzner automatic backups are ON for `orbital` (since 2026-10-12, 20% of the server
price, window 02-06 UTC, seven rolling daily images of the whole disk, listed by
`hcloud image list --type backup`). They are crash-consistent: a restored Postgres reports
"database system was not properly shut down" and replays WAL, which took under a second in
the rehearsal. The `pgdata` volume holds all state. For a logical copy as well, re-run
`seed/dump-local.sh`-style dumps on the box against the `db` service.

**Restore rehearsal (done 2026-10-16, 9 minutes, under 2 cents):** restore to a SECOND server,
never over `orbital`, and neutralise the clone the moment SSH answers, because it boots as a
full copy of production with every container on `restart: unless-stopped`: it would pull every
feed from a second IP, page the ntfy topic with any `!!` line, and Caddy would start ACME for
the real hostnames against DNS that still points at `orbital` (failed validations count toward
Let's Encrypt's rate limit). So do not start the clone within 15 minutes of 07:10 or 19:10 UTC,
and run the ssh step straight after the create returns.

```bash
hcloud server create --name restore-rehearsal --type cx33 --location hel1 \
    --image <backup image id> --ssh-key vib-laptop          # ~1 min to boot
ssh root@<clone ip> 'crontab -r; cd /root/apps/space/deploy; docker compose stop caddy;
    sed -i "s/^ALERT_URL=.*/ALERT_URL=/" .env'               # no nightly, no alerts, no TLS
ssh root@<clone ip> 'docker logs orbital-db 2>&1 | grep -E "redo done|ready to accept"
    docker exec orbital-db psql -U oei -d oei -Atc "select count(*) from satellite"
    docker exec orbital-oei-api python -c "import urllib.request as u; print(u.urlopen(\"http://127.0.0.1:8600/api/stats\").read()[:80])"
    docker exec orbital-exo-api python -c "import urllib.request as u; print(u.urlopen(\"http://127.0.0.1:8700/api/stats\").read()[:80])"'
hcloud server delete restore-rehearsal                    # leave the account's backup images alone
```

Counts should match the nightly that ran just before the image's creation time (Hetzner
timestamps backups in the account's timezone). A real disaster recovery differs in
three ways: keep the crontab and ALERT_URL (the new box IS production now), leave Caddy
running, and repoint the two DNS A records to the new IP; the old server's certificates are
in the image, so TLS resumes without a new issuance.

**TLS not issuing?** Check DNS resolves to the box (`dig orbital.<domain>`), ports 80/443
are open (Hetzner firewall off by default), and `docker compose logs caddy`.
