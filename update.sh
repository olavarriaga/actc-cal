#!/bin/sh
# Runs build.py from this machine (Cloudflare lets home IPs reach the schedules API) and publishes.
set -e
cd "$(dirname "$0")"
git pull -q --rebase
python3 build.py
git add docs
git diff --cached --quiet || { git commit -qm "Update calendars (local)"; git push -q; }
