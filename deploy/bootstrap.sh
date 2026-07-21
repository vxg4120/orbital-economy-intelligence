#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 24.04 (arm64) Hetzner box.
# Installs Docker, clones both repos as siblings under ~/apps, seeds a .env.
set -euo pipefail

APPS_DIR=${APPS_DIR:-$HOME/apps}
SPACE_REPO=${SPACE_REPO:-https://github.com/vxg4120/orbital-economy-intelligence.git}
EXO_REPO=${EXO_REPO:-https://github.com/vxg4120/exodossier.git}

echo "== Docker Engine + compose plugin =="
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" || true

echo "== Clone repos (siblings) into $APPS_DIR =="
mkdir -p "$APPS_DIR"
[ -d "$APPS_DIR/space" ]      || git clone "$SPACE_REPO" "$APPS_DIR/space"
[ -d "$APPS_DIR/exodossier" ] || git clone "$EXO_REPO"   "$APPS_DIR/exodossier"

DEPLOY="$APPS_DIR/space/deploy"
cd "$DEPLOY"
[ -f .env ] || cp .env.example .env

cat <<EOF

== Next steps ==
1) Edit $DEPLOY/.env   (POSTGRES_PASSWORD, BASE_DOMAIN, ACME_EMAIL, optional creds)
2) DNS: A records  orbital.<BASE_DOMAIN>  and  exo.<BASE_DOMAIN>  ->  this box's IP
3) docker compose up -d --build db          # start just the database
4) Copy dumps into $DEPLOY/seed/ then:  ./seed/restore-remote.sh
5) docker compose up -d --build             # apis + caddy (TLS auto-provisions)
6) Optional nightly cron — see deploy/README.md

(You may need to log out/in once for docker group membership to take effect.)
EOF
