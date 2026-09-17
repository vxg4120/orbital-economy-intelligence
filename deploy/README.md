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

**Cost:** Hetzner CAX11 (ARM, 2 vCPU / 4 GB / 40 GB) ≈ €4.5/mo · domain ≈ $10–12/yr.
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

**Backups:** re-run `seed/dump-local.sh`-style dumps on the box against the `db` service,
or snapshot the Hetzner volume. The `pgdata` volume holds all state.

**TLS not issuing?** Check DNS resolves to the box (`dig orbital.<domain>`), ports 80/443
are open (Hetzner firewall off by default), and `docker compose logs caddy`.
