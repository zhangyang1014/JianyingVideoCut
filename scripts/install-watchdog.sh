#!/bin/bash
# Install GoldenClip launchd services. Backend and frontend are kept running
# continuously; launchd restarts either one as soon as it exits.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BACKEND_LABEL="com.goldenclip.backend"
FRONTEND_LABEL="com.goldenclip.frontend"
HEALTH_LABEL="com.goldenclip.healthcheck"
OLD_WATCH_LABEL="com.goldenclip.watch-services"
CRON_MARKER="# GoldenClip watchdog"
USER_ID="$(id -u)"

mkdir -p "$PROJECT_ROOT/.dev" "$LAUNCH_AGENTS_DIR"
chmod +x "$SCRIPT_DIR/run-backend.sh" "$SCRIPT_DIR/run-frontend.sh" "$SCRIPT_DIR/watch-services.sh"

# Remove previous watchdog mechanisms so only launchd owns the dev services.
launchctl bootout "gui/$USER_ID/$OLD_WATCH_LABEL" 2>/dev/null || true
rm -f "$LAUNCH_AGENTS_DIR/$OLD_WATCH_LABEL.plist"

tmp_cron="$(mktemp)"
crontab -l 2>/dev/null | awk -v marker="$CRON_MARKER" 'index($0, marker) == 0' >"$tmp_cron" || true
if [ -s "$tmp_cron" ]; then
  crontab "$tmp_cron"
else
  crontab -r 2>/dev/null || true
fi
rm -f "$tmp_cron"

write_plist() {
  local label="$1"
  local runner="$2"
  local stdout_log="$3"
  local stderr_log="$4"
  local plist="$LAUNCH_AGENTS_DIR/$label.plist"

  cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$runner</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>ExitTimeOut</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>$stdout_log</string>
  <key>StandardErrorPath</key>
  <string>$stderr_log</string>
</dict>
</plist>
EOF

  plutil -lint "$plist" >/dev/null
}

write_plist \
  "$BACKEND_LABEL" \
  "$SCRIPT_DIR/run-backend.sh" \
  "$PROJECT_ROOT/.dev/backend.launchd.out.log" \
  "$PROJECT_ROOT/.dev/backend.launchd.err.log"

write_plist \
  "$FRONTEND_LABEL" \
  "$SCRIPT_DIR/run-frontend.sh" \
  "$PROJECT_ROOT/.dev/frontend.launchd.out.log" \
  "$PROJECT_ROOT/.dev/frontend.launchd.err.log"

cat >"$LAUNCH_AGENTS_DIR/$HEALTH_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$HEALTH_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SCRIPT_DIR/watch-services.sh</string>
    <string>once</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>60</integer>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>$PROJECT_ROOT/.dev/healthcheck.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT_ROOT/.dev/healthcheck.launchd.err.log</string>
</dict>
</plist>
EOF
plutil -lint "$LAUNCH_AGENTS_DIR/$HEALTH_LABEL.plist" >/dev/null

for label in "$BACKEND_LABEL" "$FRONTEND_LABEL" "$HEALTH_LABEL"; do
  launchctl bootout "gui/$USER_ID/$label" 2>/dev/null || true
done
sleep 1

for label in "$BACKEND_LABEL" "$FRONTEND_LABEL" "$HEALTH_LABEL"; do
  bootstrapped=false
  launchctl enable "gui/$USER_ID/$label"
  for _ in 1 2 3 4 5; do
    if launchctl bootstrap "gui/$USER_ID" "$LAUNCH_AGENTS_DIR/$label.plist" 2>/dev/null; then
      bootstrapped=true
      break
    fi
    sleep 1
  done
  if [ "$bootstrapped" != "true" ]; then
    echo "❌ 无法注册 $label" >&2
    exit 1
  fi
done

echo "✅ GoldenClip 常驻服务已安装"
echo "   方式: launchd KeepAlive"
echo "   前端: $FRONTEND_LABEL"
echo "   后端: $BACKEND_LABEL"
echo "   健康检查: ${HEALTH_LABEL}（每 60 秒，连续两次失败才重启）"
echo "   日志: $PROJECT_ROOT/.dev/*.launchd.*.log"
echo "   卸载: bash $SCRIPT_DIR/uninstall-watchdog.sh"
