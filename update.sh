#!/bin/sh
# Runs build.py from this machine (Cloudflare lets home IPs reach the schedules API) and publishes.
# With --weekend, does nothing unless this weekend's race still lacks its published times.
set -e
cd "$(dirname "$0")"
git pull -q --rebase
if [ "$1" = --weekend ] && ! python3 build.py --need-times; then exit 0; fi
python3 build.py
git add docs
git diff --cached --quiet || { git commit -qm "Update calendars (local)"; git push -q; }
