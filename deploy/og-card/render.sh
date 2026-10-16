#!/bin/sh
# Re-render the share card: deploy/og-card/card.html -> deploy/landing/og.png (1200x630).
set -e
cd "$(dirname "$0")"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=1 --window-size=1200,630 --allow-file-access-from-files \
  --virtual-time-budget=3000 --screenshot="$PWD/../landing/og.png" "file://$PWD/card.html"
