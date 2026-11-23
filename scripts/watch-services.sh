#!/bin/bash
# GoldenClip dev services watchdog — checks every 5 minutes and auto-starts
# backend (8000) + frontend (5177) when they are down.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_DIR="$PROJECT_ROOT/.dev"
LOG_FILE="$DEV_DIR/watch-services.log"
WATCH_PID_FILE="$DEV_DIR/watch-services.pid"
BACKEND_PID_FILE="$DEV_DIR/backend.pid"
FRONTEND_PID_FILE="$DEV_DIR/frontend.pid"
INTERVAL="${WATCH_INTERVAL_SEC:-300}"
BACKEND_HEALTH_TIMEOUT_SEC="${BACKEND_HEALTH_TIMEOUT_SEC:-10}"

BACKEND_URL="http://localhost:8000/api/health"
FRONTEND_URL="http://localhost:5177/"

mkdir -p "$DEV_DIR"

# launchd starts with a minimal PATH, so include common local Node locations.
export PATH="$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

backend_healthy() {
  # 大任务偶尔需要同步保存体积较大的本地 JSON；3 秒阈值会把短暂忙碌误判为宕机，
  # 继而强杀仍在运行的审计。连续检查仍保留，但单次允许合理的落盘时间。
  curl -sf --max-time "$BACKEND_HEALTH_TIMEOUT_SEC" "$BACKEND_URL" >/dev/null 2>&1
}

frontend_healthy() {
  local code
  code="$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' "$FRONTEND_URL" 2>/dev/null || echo 000)"
  [ "$code" = "200" ] || [ "$code" = "304" ]
}

pid_alive() {
  local pid="${1:-}"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

launchd_state() {
  local label="$1"
  launchctl print "gui/$(id -u)/$label" 2>/dev/null | awk -F'= ' '/state =/{print $2; exit}'
}

launchd_loaded() {
  launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1
}

start_backend() {
  if backend_healthy; then
    return 0
  fi

  if launchd_loaded "com.goldenclip.backend"; then
    log "Restarting backend through launchd"
    launchctl kickstart -k "gui/$(id -u)/com.goldenclip.backend"
    sleep 3
    return
  fi

  log "Starting unmanaged backend on :8000 (launchd is not installed)"
  nohup bash "$SCRIPT_DIR/run-backend.sh" >>"$DEV_DIR/backend.log" 2>&1 &
  echo "$!" >"$BACKEND_PID_FILE"
  sleep 2
}

start_frontend() {
  if frontend_healthy; then
    return 0
  fi

  if launchd_loaded "com.goldenclip.frontend"; then
    log "Restarting frontend through launchd"
    launchctl kickstart -k "gui/$(id -u)/com.goldenclip.frontend"
    sleep 3
    return
  fi

  cd "$PROJECT_ROOT"
  log "Starting unmanaged frontend on :5177 (launchd is not installed)"
  nohup pnpm dev >>"$DEV_DIR/frontend.log" 2>&1 &
  echo "$!" >"$FRONTEND_PID_FILE"
  sleep 3
}

ensure_services() {
  if backend_healthy; then
    log "Backend OK"
  else
    log "Backend health check failed — confirming"
    sleep 2
    if backend_healthy; then
      log "Backend recovered before restart"
    else
      log "Backend down — restarting"
      start_backend
    fi
    if backend_healthy; then
      log "Backend recovered"
    else
      log "Backend failed to start (see $DEV_DIR/backend.log)"
    fi
  fi

  if frontend_healthy; then
    log "Frontend OK"
  else
    log "Frontend health check failed — confirming"
    sleep 2
    if frontend_healthy; then
      log "Frontend recovered before restart"
    else
      log "Frontend down — restarting"
      start_frontend
    fi
    if frontend_healthy; then
      log "Frontend recovered"
    else
      log "Frontend failed to start (see $DEV_DIR/frontend.log)"
    fi
  fi
}

start_daemon() {
  if [ -f "$WATCH_PID_FILE" ]; then
    local old_pid
    old_pid="$(cat "$WATCH_PID_FILE" 2>/dev/null || true)"
    if pid_alive "$old_pid"; then
      log "Watchdog already running (PID $old_pid)"
      exit 0
    fi
  fi

  log "Watchdog daemon starting (interval ${INTERVAL}s)"
  (
    log "Watch loop active — checking every ${INTERVAL}s"
    while true; do
      ensure_services
      sleep "$INTERVAL"
    done
  ) >>"$LOG_FILE" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  echo "$pid" >"$WATCH_PID_FILE"
  log "Watchdog daemon started (PID $pid)"
}

stop_daemon() {
  if [ -f "$WATCH_PID_FILE" ]; then
    local pid
    pid="$(cat "$WATCH_PID_FILE" 2>/dev/null || true)"
    if pid_alive "$pid"; then
      kill "$pid" 2>/dev/null || true
      log "Watchdog stopped (PID $pid)"
    fi
    rm -f "$WATCH_PID_FILE"
  else
    log "Watchdog not running"
  fi
}

watch_loop() {
  log "Watch loop active — checking every ${INTERVAL}s"
  while true; do
    ensure_services
    sleep "$INTERVAL"
  done
}

case "${1:-once}" in
  once)
    ensure_services
    ;;
  watch)
    watch_loop
    ;;
  start)
    start_daemon
    ensure_services
    ;;
  stop)
    stop_daemon
    ;;
  status)
    echo "Backend:  $(backend_healthy && echo OK || echo DOWN)  $BACKEND_URL"
    echo "Frontend: $(frontend_healthy && echo OK || echo DOWN)  $FRONTEND_URL"
    backend_launchd_state="$(launchd_state com.goldenclip.backend)"
    frontend_launchd_state="$(launchd_state com.goldenclip.frontend)"
    healthcheck_launchd_state="$(launchd_state com.goldenclip.healthcheck)"
    if [ -n "$backend_launchd_state" ] && [ -n "$frontend_launchd_state" ] && [ -n "$healthcheck_launchd_state" ]; then
      echo "Watchdog: launchd LOADED (backend=$backend_launchd_state, frontend=$frontend_launchd_state, healthcheck=$healthcheck_launchd_state)"
    elif [ -n "$backend_launchd_state" ] && [ -n "$frontend_launchd_state" ]; then
      echo "Watchdog: launchd SERVICES ONLY (healthcheck not installed)"
    elif crontab -l 2>/dev/null | grep -q "# GoldenClip watchdog"; then
      echo "Watchdog: cron ACTIVE (legacy, every 300s)"
    elif launchctl print "gui/$(id -u)/com.goldenclip.watch-services" >/dev/null 2>&1; then
      echo "Watchdog: launchd LOADED (legacy, every ${INTERVAL}s)"
    elif [ -f "$WATCH_PID_FILE" ] && pid_alive "$(cat "$WATCH_PID_FILE")"; then
      echo "Watchdog: RUNNING (PID $(cat "$WATCH_PID_FILE"), interval ${INTERVAL}s)"
    else
      echo "Watchdog: STOPPED (run: bash scripts/install-watchdog.sh)"
    fi
    ;;
  *)
    echo "Usage: $0 {once|watch|start|stop|status}"
    exit 1
    ;;
esac
