# Landing follow-up verification — 2026-09-17

Base `ae3454e` already includes the user-confirmed first QA year2014.

## Finding and change

The public unknown-path response was404 with zero bytes, although `/404.html`
returned200 with5764 bytes. Running the full repository Caddyfile locally under
Caddy2.11.4 returned the correct404 recovery page. The routing itself is valid.

Compose mounted a single Caddyfile. The official Caddy image docs warn that atomic
file replacement can leave a single-file mount reading the old inode even after
reload. Changed to `deploy/caddy/Caddyfile` with `./caddy:/etc/caddy:ro`, preserving
the Caddyfile contents. This removes that deployment weakness; it does not prove
which config/inode the public container currently has. Docker daemon was stopped,
so container-mount behavior was not exercised locally.

The operator runbook now distinguishes one-time Caddy-only recreation for the mount
change from subsequent checked hash comparison, validation, reload and HTTP smoke.
Named TLS/config volumes are preserved. No operator command was run by Codex.

Mobile menu anchor navigation previously removed the open class while leaving
aria-expanded=true. The shared menu link handler now closes both, and the button
declares aria-controls. First QA2014 and employer dates remain intact.

## Verification

- Official Caddy2.11.4 mac-arm64 archive downloaded to `/tmp/codex-caddy-followup-20260917`;
  SHA512 matched the official release checksums. No global installation.
- Actual repo Caddyfile exercised with only loopback domains/ports, temporary log
  paths and local fixture upstreams substituted; no simplified routing mirror.
- `CADDY_BIN=/tmp/codex-caddy-followup-20260917/caddy DATABASE_URL=intentionally-invalid-no-database
  /Users/vgupta/Development/repos/space/.venv/bin/python -m pytest tests/test_landing_caddy.py -q`
  — **3 passed**. Includes unknown path/trailing slash, ordinary page/image,
  direct404 asset, upstream-returned404 body, unreachable-upstream502, missing
  recovery asset, and smoke-check failure when recovery HTML is absent.
- `python3 scripts/check_landing.py http://127.0.0.1:58651` —5 HTTP checks passed.
- Docker Compose config rendered with dummy values, without contacting a daemon;
  verified read-only directory mount at `/etc/caddy`. No actual container recreation.
- Ruff on new Python files and `git diff --check` passed.
- Standalone isolated Chromium145.0.7632.6,1440px/375px and375px with JavaScript
  disabled: home, fun and splitstep200; no horizontal overflow or page errors.
  No-JS home revealed all43 sections; failed live metrics showed cached September2026.
  Mobile menu opened with expanded=true and closed after the work link with
  expanded=false, no open class, hash=#work. Unknown path displayed styled404.
  Screenshots inspected: mobile recovery and open menu.
- Browser evidence: ignored `output/audit/landing-followup/results.json` and PNGs.
  Harness: initial review checkout `tasks/verify-landing.cjs`, invoked with
  `AUDIT_BASE_URL=http://127.0.0.1:58651` and this checkout's evidence output path.

## Release boundary

Source/local validation only. Active production config/hash, directory mount
adoption and subsequent public smoke are operator verification. A200 response for
the standalone404 file is insufficient. Required independent integrated Codex
review is recorded separately after commits.
