#!/usr/bin/env bash
set -euo pipefail

# Default production flow:
# collect sessions -> select roles -> export structured
#
# Usage:
#   bash scripts/openclaw_structured_export.sh --all
#   bash scripts/openclaw_structured_export.sh --role "张三"
#   bash scripts/openclaw_structured_export.sh --roles "张三,李四"
# Optional:
#   --page-size 100

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
PAGE_SIZE="100"
ROLE_MODE=""
ROLE_VALUE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ROLE_MODE="all"
      shift
      ;;
    --role)
      ROLE_MODE="include"
      ROLE_VALUE="${2:-}"
      shift 2
      ;;
    --roles)
      ROLE_MODE="include"
      ROLE_VALUE="${2:-}"
      shift 2
      ;;
    --page-size)
      PAGE_SIZE="${2:-100}"
      shift 2
      ;;
    *)
      echo "[flow] unknown arg: $1"
      exit 2
      ;;
  esac
done

if [ -z "$ROLE_MODE" ]; then
  echo "[flow] choose one role mode: --all | --role <name> | --roles <csv>"
  exit 2
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "[flow] venv not found. run: bash scripts/openclaw_setup.sh"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[flow] step 1/4 start chrome (headless)"
imx chrome start

echo "[flow] step 2/4 collect sessions"
imx run collect --page-size "$PAGE_SIZE"

echo "[flow] step 3/4 select roles"
if [ "$ROLE_MODE" = "all" ]; then
  imx roles select --all
else
  if [ -z "$ROLE_VALUE" ]; then
    echo "[flow] missing role value for --role/--roles"
    exit 2
  fi
  imx roles select --include "$ROLE_VALUE"
fi

echo "[flow] step 4/4 export structured (json/markdown based on plugin format config)"
imx run export --kind structured

echo "[flow] done."

