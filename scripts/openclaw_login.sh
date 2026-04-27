#!/usr/bin/env bash
set -euo pipefail

# First-time login bootstrap (manual step).
# Usage:
#   bash scripts/openclaw_login.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"

if [ ! -d "$VENV_DIR" ]; then
  echo "[login] venv not found. run: bash scripts/openclaw_setup.sh"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[login] starting headed Chrome..."
imx chrome start --headed

echo "[login] please complete website login in opened Chrome window."
imx auth login

echo "[login] done. profile persisted."

