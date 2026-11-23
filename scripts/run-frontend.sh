#!/bin/bash
# Run GoldenClip Vite frontend under launchd.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export PATH="$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

cd "$PROJECT_ROOT"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting GoldenClip frontend"
exec pnpm dev
