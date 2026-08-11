"""Deprecated name; delegates to scripts/refresh_matviews.py.

Kept for one deploy cycle because scripts/ is baked into the docker image while deploy/ is a
bind mount: a box that has pulled the nightly-refresh.sh rename but not yet rebuilt the image
would otherwise call a script that does not exist inside its container (and the nightly's
`|| echo` soft-fail would bury the error in the log). Remove once every environment's image
postdates the rename.
"""

from refresh_matviews import main

if __name__ == "__main__":
    raise SystemExit(main())
