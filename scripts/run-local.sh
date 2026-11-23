#!/usr/bin/env bash
# Run the platform without Docker (macOS or Linux). Needs: git, node >= 22.13, uv.
#   brew install node@22 uv        (macOS)
#
#   ./scripts/run-local.sh              build everything and start on http://localhost:3000
#   ./scripts/run-local.sh cli "What is 17% of 2340?"    run one prompt through Hermes
#   ./scripts/run-local.sh cli --harness baseline --task skill-01 --skills acme-billing
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_DIR="${HERMES_DIR:-$HOME/hermes-agent}"          # where Hermes Agent gets installed
HERMES_COMMIT=611f0ab757b307774c4a63c7b19f67d1f3aa6f6e   # same pin as the Dockerfile
PY="$HERMES_DIR/.venv/bin/python"

# 1. Hermes Agent in its own virtualenv (first run only, takes a few minutes).
if [ ! -x "$PY" ]; then
  echo "Installing Hermes Agent into $HERMES_DIR ..."
  git init -q "$HERMES_DIR"
  git -C "$HERMES_DIR" remote add origin https://github.com/NousResearch/hermes-agent.git 2>/dev/null || true
  git -C "$HERMES_DIR" fetch -q --depth 1 origin "$HERMES_COMMIT"
  git -C "$HERMES_DIR" checkout -q FETCH_HEAD
  (cd "$HERMES_DIR" && uv sync --frozen --no-dev -p 3.14)    # uv downloads Python 3.14 if needed
  uv pip install --python "$PY" "pyyaml>=6"                  # our worker needs pyyaml too
fi

# 2. API keys from .env (OPENROUTER_API_KEY, ...).
if [ -f "$REPO/.env" ]; then set -a; source "$REPO/.env"; set +a; else echo "No .env found: copy .env.example to .env"; fi

export EVALS_ROOT="${EVALS_ROOT:-$REPO}" PYTHONPATH="$HERMES_DIR"

# One prompt from the terminal, no server needed.
if [ "${1:-}" = "cli" ]; then
  shift
  exec "$PY" "$REPO/worker/cli.py" "$@"
fi

# 3. Build the UI (served by the API) and the API, then start.
(cd "$REPO/ui" && npm ci --no-fund --no-audit && npm run build)
(cd "$REPO/api" && npm ci --no-fund --no-audit && npm run build)
export WORKER_CMD="$PY $REPO/worker/run_trial.py" DB_PATH="$REPO/data/evals.db" UI_DIR="$REPO/ui/dist"
cd "$REPO/api" && exec node dist/main.js
