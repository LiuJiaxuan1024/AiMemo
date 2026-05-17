#!/usr/bin/env bash
set -euo pipefail

SKIP_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --skip-install)
      SKIP_INSTALL=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./scripts/start-backend.sh [--skip-install]" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"

cd "$BACKEND_DIR"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Creating backend virtual environment..."
  if command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv .venv
  else
    python3 -m venv .venv
  fi
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  echo "Installing backend dependencies..."
  "$VENV_PYTHON" -m pip install -U pip
  "$VENV_PYTHON" -m pip install -e ".[dev]"
fi

echo "Starting AiMemo backend at http://127.0.0.1:8000 ..."
"$VENV_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
