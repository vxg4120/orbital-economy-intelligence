#!/usr/bin/env bash
# Bake the landing page's cached numbers from the live APIs.
#
# deploy/landing/index.html ships hardcoded copies of the numbers its own script fetches from
# /live/orbital and /live/exo: the four-tile strip under "Proof, not promises" and the two
# rails under "The work". They are what the first paint, scrapers, and a no-JS reader see, and
# what a tile falls back to when its platform is down, so they should equal the live values as
# of the last deploy rather than whatever was last typed by hand. This fetches both endpoints,
# formats every value exactly as the page script would render it, rewrites the markup in place,
# and moves the snapshot date (the HTML comment above the strip) and the cached stamp's month.
#
# Run it right before a deploy, from any machine that can reach the site, then commit:
#
#   deploy/landing-snapshot.sh                      # against https://$BASE_DOMAIN from deploy/.env,
#                                                   # or https://vibcreates.com when that is unset
#   deploy/landing-snapshot.sh https://other.host   # any origin serving /live/orbital + /live/exo
#   git add deploy/landing/index.html && git commit -m "Landing: snapshot $(date -u +%F)"
#
# then pull on the box as usual (README, "Update after a git push"); landing/ is bind-mounted
# into Caddy, so no image rebuild is involved. Idempotent: a second run against unchanged
# numbers writes nothing. Either fetch failing aborts before any write, so the file is never
# half-baked. Needs curl and python3.
set -euo pipefail
cd "$(dirname "$0")"                  # deploy/
set -a; [ -f .env ] && . ./.env; set +a

HTML=landing/index.html
BASE_URL="${1:-${LANDING_BASE_URL:-https://${BASE_DOMAIN:-vibcreates.com}}}"
BASE_URL="${BASE_URL%/}"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for ep in orbital exo; do
  curl -fsS --max-time 20 "$BASE_URL/live/$ep" -o "$tmp/$ep.json" \
    || { echo "landing-snapshot: fetch of $BASE_URL/live/$ep failed; nothing written" >&2; exit 1; }
done

python3 - "$HTML" "$tmp/orbital.json" "$tmp/exo.json" <<'PY'
import datetime, json, re, sys
from decimal import Decimal, ROUND_HALF_UP

html_path, orbital_path, exo_path = sys.argv[1:4]
o = json.load(open(orbital_path)); e = json.load(open(exo_path))

def num(d, *path):
    """The finite number at d[path...], else None (the page's complete() check, one key at a time)."""
    v = d
    for k in path:
        if not isinstance(v, dict) or k not in v: return None
        v = v[k]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float('inf'), float('-inf')): return None
    return v

# Each formatter mirrors the page script, so a baked value is exactly what the live fetch would
# render over it. Rounding is half-up like JS toFixed / Math.round, not Python's half-even.
def group(v): return f"{int(v):,}"                                                  # toLocaleString('en-US')
def mega(v):  return str(Decimal(v / 1e6).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)) + 'M'   # (v/1e6).toFixed(1)+'M'
def fmt(v):   return mega(v) if v >= 1e6 else group(v)                              # page fmt()
def jsnum(v): return str(int(v)) if float(v).is_integer() else repr(float(v))       # 100 -> "100", 99.9 -> "99.9"
def kilo(v):  return str(int(Decimal(v / 1e3).quantize(Decimal('1'), rounding=ROUND_HALF_UP))) + 'k'  # Math.round(v/1e3)+'k'

tiles = {}
sat, gp = num(o, 'satellites'), num(o, 'gp_elements')
if sat is None or gp is None: sys.exit("landing-snapshot: /live/orbital lacks satellites/gp_elements; nothing written")
tiles['s-sat'] = tiles['r-sat'] = group(sat)
tiles['s-gp'] = fmt(gp); tiles['r-gp'] = mega(gp)
op = num(o, 'coverage', 'operator_pct')
if op is not None: tiles['r-op'] = jsnum(op) + '%'
oc = [num(o, 'conflicts', k) for k in ('status', 'decay', 'stale_owners')]
if None not in oc and sum(oc): tiles['r-conf'] = group(sum(oc))

stars, cand, sa = num(e, 'stars'), num(e, 'candidates'), num(e, 'source_assertions')
if stars is None or cand is None or sa is None: sys.exit("landing-snapshot: /live/exo lacks stars/candidates/source_assertions; nothing written")
tiles['s-cand'] = tiles['r-cand'] = group(cand); tiles['r-star'] = group(stars)
tiles['s-assert'] = fmt(sa); tiles['r-assert'] = kilo(sa)
ec = [num(e, 'conflicts', k) for k in ('radius', 'disposition', 'teff')]
if None not in ec and sum(ec): tiles['r-dconf'] = group(sum(ec))

src = open(html_path, encoding='utf-8').read(); out = src; changed = []
for tid, val in tiles.items():
    rx = re.compile(r'(id="%s">)([^<]*)(<)' % re.escape(tid))
    hits = rx.findall(out)
    if len(hits) != 1: sys.exit(f'landing-snapshot: expected exactly one id="{tid}" tile, found {len(hits)}; nothing written')
    if hits[0][1] != val: changed.append(f"  {tid:9} {hits[0][1]} -> {val}")
    out = rx.sub(lambda m: m.group(1) + val + m.group(3), out)

today = datetime.datetime.now(datetime.timezone.utc)
months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
for pat, val, what in [
    (r'(cached fallback, snapshot )\d{4}-\d{2}-\d{2}', today.strftime('%Y-%m-%d'), 'snapshot date'),
    (r'(cached · )[A-Z][a-z]+ \d{4}', f"{months[today.month - 1]} {today.year}", 'cached stamp'),
]:
    rx = re.compile(pat)
    hits = rx.findall(out)
    if len(hits) != 1: sys.exit(f"landing-snapshot: expected exactly one {what} in the markup, found {len(hits)}; nothing written")
    new = rx.sub(lambda m: m.group(1) + val, out)
    if new != out: changed.append(f"  {what}: -> {val}")
    out = new

if out == src:
    print("landing-snapshot: already current, nothing to write"); sys.exit(0)
open(html_path, 'w', encoding='utf-8').write(out)
print("landing-snapshot: wrote " + html_path + "\n" + "\n".join(changed))
PY
