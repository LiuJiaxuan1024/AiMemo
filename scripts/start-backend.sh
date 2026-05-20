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

python_is_312() {
  local python_exe="$1"
  [[ -x "$python_exe" ]] || return 1
  "$python_exe" - <<'PY' 2>/dev/null
import sys
raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)
PY
}

find_python312() {
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi
  if command -v python >/dev/null 2>&1 && python - <<'PY' 2>/dev/null
import sys
raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)
PY
  then
    command -v python
    return 0
  fi
  return 1
}

cd "$BACKEND_DIR"

if ! python_is_312 "$VENV_PYTHON"; then
  PYTHON312="$(find_python312 || true)"
  if [[ -z "$PYTHON312" ]]; then
    cat >&2 <<'EOF'
Python 3.12 is required, but it was not found.

Install it first, then rerun this script. Examples:
  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv
  Fedora:        sudo dnf install python3.12
  macOS:         brew install python@3.12
EOF
    exit 1
  fi

  if [[ -d ".venv" ]]; then
    echo "Existing backend virtual environment is not Python 3.12. Recreating .venv..."
    rm -rf .venv
  else
    echo "Creating backend virtual environment with Python 3.12..."
  fi
  "$PYTHON312" -m venv .venv
fi

if ! python_is_312 "$VENV_PYTHON"; then
  echo "Backend virtual environment was created, but it is not Python 3.12." >&2
  exit 1
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  echo "Installing backend dependencies..."
  "$VENV_PYTHON" -m pip install -U pip
  "$VENV_PYTHON" -m pip install -e ".[dev]"
fi

echo "Starting AiMemo gateway at http://127.0.0.1:8000 ..."
echo "AiMemo app will be available at http://127.0.0.1:8000/app after frontend build."
"$VENV_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
