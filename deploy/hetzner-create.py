#!/usr/bin/env python3
"""
Create the orbital deploy box on Hetzner Cloud, retrying across locations and
server types until stock is found (Hetzner's cheap 4 GB tiers wink in and out).

Run in YOUR terminal so the API token never leaves your machine:

    export HCLOUD_TOKEN='paste-your-token-here'
    python3 hetzner-create.py

Prints the new server's public IPv4 on success. Stdlib only, no pip installs.
Cost is bounded to the tiers you already approved (<= ~$9.59/mo): it tries the
cheapest 4 GB boxes first and only falls back to the 8 GB CX33 if all 4 GB are
out. It will NOT create anything pricier than that.
"""
import json, os, sys, urllib.request, urllib.error

TOKEN = os.environ.get("HCLOUD_TOKEN")
if not TOKEN:
    sys.exit("Set your token first:  export HCLOUD_TOKEN='...'")

API = "https://api.hetzner.cloud/v1"
NAME = os.environ.get("SERVER_NAME", "orbital")
IMAGE = os.environ.get("IMAGE", "ubuntu-24.04")
KEY_NAME = os.environ.get("KEY_NAME", "vib-laptop")
PUBKEY = os.path.expanduser(os.environ.get("PUBKEY_FILE", "~/.ssh/id_rsa.pub"))

def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or "{}")
        except Exception:
            return e.code, {"error": {"message": f"HTTP {e.code}"}}

# 1) Ensure the SSH key is uploaded (idempotent)
st, j = api("GET", f"/ssh_keys?name={KEY_NAME}")
if not j.get("ssh_keys"):
    pub = open(PUBKEY).read().strip()
    st, j = api("POST", "/ssh_keys", {"name": KEY_NAME, "public_key": pub})
    if st >= 300:
        sys.exit("SSH key upload failed: " + json.dumps(j))
    print(f"Uploaded SSH key '{KEY_NAME}' from {PUBKEY}")
else:
    print(f"SSH key '{KEY_NAME}' already present")

# 2) Create the server, retrying across combos. Cheapest 4 GB first, 8 GB last.
#    (all <= the ~$9.59/mo you approved; pricier CPX31/CAX21 intentionally excluded)
TYPES = ["cx23", "cx22", "cax11", "cpx21", "cx33"]
LOCS  = ["hel1", "fsn1", "nbg1", "ash", "hil"]

print(f"\nCreating '{NAME}' (image {IMAGE}) — trying combos until one has stock:\n")
for t in TYPES:
    for l in LOCS:
        st, j = api("POST", "/servers", {
            "name": NAME, "server_type": t, "image": IMAGE, "location": l,
            "ssh_keys": [KEY_NAME],
            "public_net": {"enable_ipv4": True, "enable_ipv6": True}})
        srv = j.get("server")
        if st < 300 and srv:
            ip = srv["public_net"]["ipv4"]["ip"]
            print(f"\n  SUCCESS  type={t}  location={l}")
            print(f"  PUBLIC IP: {ip}\n")
            print(f"  Next: give this IP to Claude, then:  ssh root@{ip}")
            sys.exit(0)
        msg = (j.get("error") or {}).get("message", json.dumps(j))
        print(f"  {t:6} {l:5} -> {msg[:90]}")

print("\nNo stock in any combo right now. Re-run this in a few minutes — "
      "Hetzner's cheap tiers restock intermittently.")
sys.exit(1)
