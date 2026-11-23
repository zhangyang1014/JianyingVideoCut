#!/bin/bash
# Run the GoldenClip FastAPI backend.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export PATH="$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PYTHONPATH="$PROJECT_ROOT"
export MODELSCOPE_CACHE="$PROJECT_ROOT/models"

if [ -f "$PROJECT_ROOT/local.env" ]; then
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/local.env"
fi

cd "$PROJECT_ROOT"
python_bin=python3
if [ -d "$PROJECT_ROOT/.venv" ]; then
  python_bin="$PROJECT_ROOT/.venv/bin/python"
fi
if ! "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "GoldenClip 需要 Python 3.10+。请用受支持的 Python 创建项目的 .venv。" >&2
  exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting GoldenClip backend ($("$python_bin" --version))"
uvicorn_args=(
  backend.main:app
  --host 0.0.0.0
  --port 8000
  --timeout-graceful-shutdown 10
)
# launchd 常驻模式禁用 reload，避免 reloader 父子进程与 KeepAlive 互相争抢端口。
# 手工开发若确需热更新，可显式设置 GOLDENCLIP_RELOAD=1。
if [ "${GOLDENCLIP_RELOAD:-0}" = "1" ]; then
  uvicorn_args+=(--reload)
fi
exec "$python_bin" -m uvicorn "${uvicorn_args[@]}"
