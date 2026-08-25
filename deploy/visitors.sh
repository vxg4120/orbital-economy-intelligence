#!/usr/bin/env bash
# Who came to the sites, from Caddy's own JSON access logs. Free, zero third parties, and it
# works before any analytics account exists. Run on the box:
#
#   /root/apps/space/deploy/visitors.sh            # last 7 days
#   /root/apps/space/deploy/visitors.sh 30         # last 30 days
#
# The logs live inside the caddy container's /data volume (landing.log, orbital.log, exo.log,
# 25MiB x5 rotation). This reads them through docker compose and summarizes with the host's
# python3: unique visitors (by IP), where they came from (referers), what they read (paths),
# and a human/bot split by user agent. IPs are shown so you can look a visitor up, but nothing
# here leaves the box.
set -uo pipefail
cd "$(dirname "$0")"
DAYS="${1:-7}"

docker compose exec -T caddy sh -c 'cat /data/access/*.log* 2>/dev/null' | python3 - "$DAYS" <<'PY'
import collections
import datetime as dt
import json
import sys

days = int(sys.argv[1])
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

BOT_TOKENS = ("bot", "crawl", "spider", "slurp", "curl", "wget", "python-requests", "headless",
              "preview", "monitor", "probe", "scan", "gpt", "claude", "lighthouse")

per_host = collections.defaultdict(lambda: {
    "hits": 0, "ips": collections.Counter(), "paths": collections.Counter(),
    "referers": collections.Counter(), "agents": collections.Counter(), "bots": 0,
})

for line in sys.stdin:
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    ts = dt.datetime.fromtimestamp(r.get("ts", 0), dt.timezone.utc)
    if ts < cutoff:
        continue
    req = r.get("request", {})
    host = req.get("host", "?")
    ua = (req.get("headers", {}).get("User-Agent") or [""])[0]
    ref = (req.get("headers", {}).get("Referer") or [""])[0]
    ip = req.get("client_ip") or req.get("remote_ip") or "?"
    h = per_host[host]
    h["hits"] += 1
    if any(t in ua.lower() for t in BOT_TOKENS):
        h["bots"] += 1
        continue
    h["ips"][ip] += 1
    path = req.get("uri", "?").split("?")[0]
    if not path.startswith(("/assets", "/fonts", "/favicon")):
        h["paths"][path] += 1
    if ref and host not in ref:
        h["referers"][ref] += 1
    h["agents"][ua[:70]] += 1

if not per_host:
    print(f"no requests in the last {days} day(s); logs began when this shipped")
    sys.exit(0)

for host in sorted(per_host):
    h = per_host[host]
    human = h["hits"] - h["bots"]
    print(f"\n===== {host}: {h['hits']} requests, {human} human-ish, {h['bots']} bot, "
          f"{len(h['ips'])} unique visitors (last {days}d) =====")
    print("  top visitors (IP, requests):")
    for ip, n in h["ips"].most_common(8):
        print(f"    {ip:<40} {n}")
    print("  came from:")
    for ref, n in h["referers"].most_common(6) or [("(direct or none)", human)]:
        print(f"    {ref[:70]:<70} {n}")
    print("  read:")
    for path, n in h["paths"].most_common(8):
        print(f"    {path[:70]:<70} {n}")
PY
