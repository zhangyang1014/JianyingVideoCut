#!/bin/bash
# Uninstall GoldenClip watchdog.

set -euo pipefail

BACKEND_LABEL="com.goldenclip.backend"
FRONTEND_LABEL="com.goldenclip.frontend"
HEALTH_LABEL="com.goldenclip.healthcheck"
OLD_WATCH_LABEL="com.goldenclip.watch-services"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
CRON_MARKER="# GoldenClip watchdog"
USER_ID="$(id -u)"

for label in "$BACKEND_LABEL" "$FRONTEND_LABEL" "$HEALTH_LABEL" "$OLD_WATCH_LABEL"; do
  launchctl bootout "gui/$USER_ID/$label" 2>/dev/null || true
  rm -f "$LAUNCH_AGENTS_DIR/$label.plist"
done

tmp_cron="$(mktemp)"
crontab -l 2>/dev/null | awk -v marker="$CRON_MARKER" 'index($0, marker) == 0' >"$tmp_cron" || true
if [ -s "$tmp_cron" ]; then
  crontab "$tmp_cron"
else
  crontab -r 2>/dev/null || true
fi
rm -f "$tmp_cron"

echo "✅ GoldenClip 自动守护已卸载"
