#!/bin/bash
# Start GoldenClip in the background, or run in the foreground with --dev.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_RUNNER="$PROJECT_ROOT/scripts/run-backend.sh"

case "${1:-}" in
  --dev) GOLDENCLIP_RELOAD=1 exec bash "$BACKEND_RUNNER" ;;
  "") ;;
  *) echo "用法：bash start_backend.sh [--dev]" >&2; exit 2 ;;
esac

HEALTH_URL="http://localhost:8000/api/health"
backend_healthy() {
  curl --noproxy '*' -fs --max-time 1 "$HEALTH_URL" >/dev/null 2>&1
}

if backend_healthy; then
  echo "GoldenClip 后端已运行 → http://localhost:8000"
  exit 0
fi

LOG_FILE="$PROJECT_ROOT/.dev/backend.log"
mkdir -p "$(dirname "$LOG_FILE")"
nohup bash "$BACKEND_RUNNER" >>"$LOG_FILE" 2>&1 &
backend_pid=$!

for attempt in {1..10}; do
  if backend_healthy; then
    echo "GoldenClip 后端已启动 → http://localhost:8000"
    echo "前端 → http://localhost:5177"
    exit 0
  fi
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    echo "GoldenClip 后端启动失败，日志：$LOG_FILE" >&2
    exit 1
  fi
  sleep 1
done

echo "GoldenClip 后端在等待时间内未通过健康检查，日志：$LOG_FILE" >&2
exit 1
