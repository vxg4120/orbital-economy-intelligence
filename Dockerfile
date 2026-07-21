# Orbital Economy Intelligence — API + built SPA in one image.
# The same image serves HTTP traffic and (via `docker compose exec`) runs the
# nightly catalog refresh, since space's ingest deps are already in requirements.txt.
# Build context = repo root. Target arch matches the host (arm64 on Apple Silicon
# and on the Hetzner CAX11 ARM box).

# ---------- stage 1: build the React SPA ----------
FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@10.31.0 --activate
# Install deps against the committed lockfile first for layer caching.
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm run build          # -> /web/dist

# ---------- stage 2: python runtime ----------
FROM python:3.13-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
# psycopg[binary]/numpy ship manylinux wheels (incl. aarch64) — no compiler needed.
COPY requirements.txt ./
RUN pip install -r requirements.txt
# Source needed to SERVE (api, common, identity) and to run the nightly REFRESH
# in the same container (ingest, metrics, quality, db migrations, scripts).
COPY api/ ./api/
COPY common/ ./common/
COPY identity/ ./identity/
COPY ingest/ ./ingest/
COPY metrics/ ./metrics/
COPY quality/ ./quality/
COPY db/ ./db/
COPY scripts/ ./scripts/
COPY pyproject.toml conftest.py ./
# Built SPA — api/main.py mounts it at / when web/dist/index.html exists.
COPY --from=web /web/dist ./web/dist
EXPOSE 8600
# --host 0.0.0.0 so the container is reachable from Caddy / other containers.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8600"]
