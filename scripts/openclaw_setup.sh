#!/usr/bin/env bash
set -euo pipefail

# One-time bootstrap for openclaw/mac machines.
# Usage:
#   bash scripts/openclaw_setup.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

echo "[setup] root: $ROOT_DIR"
echo "[setup] python: $PYTHON_BIN"

if [ ! -d "$VENV_DIR" ]; then
  echo "[setup] creating venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install -U pip
python -m pip install -e .

chmod +x "$ROOT_DIR/imx" || true
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT_DIR/imx" "$HOME/.local/bin/imx"

for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  if [ -f "$rc" ] && ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$rc"; then
    printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
  fi
done

echo ""
echo "[setup] done."
echo "[setup] verify with:"
echo "  source $VENV_DIR/bin/activate"
echo "  imx --help"
echo "  # or without activate:"
echo "  $HOME/.local/bin/imx --help"
