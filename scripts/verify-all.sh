#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> backend pytest"
python3 -m pytest backend

echo "==> agent golden evals"
python3 -m backend.agent_runtime.evals

echo "==> frontend vitest"
pnpm test:run

echo "==> TypeScript check"
pnpm check

echo "==> verification complete"
