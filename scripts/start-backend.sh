#!/usr/bin/env bash
set -euo pipefail

SKIP_INSTALL=0
HOST="${AIMEMO_HOST:-127.0.0.1}"
PORT="${AIMEMO_BACKEND_PORT:-8000}"
for arg in "$@"; do
  case "$arg" in
    --skip-install)
      SKIP_INSTALL=1
      ;;
    --host=*)
      HOST="${arg#--host=}"
      ;;
    --port=*)
      PORT="${arg#--port=}"
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./scripts/start-backend.sh [--skip-install] [--host=127.0.0.1] [--port=8000]" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"
source "$SCRIPT_DIR/dev-utils.sh"

ACTUAL_PORT="$(find_available_port "$HOST" "$PORT")"
print_port_fallback "Backend" "$PORT" "$ACTUAL_PORT"
PORT="$ACTUAL_PORT"

python_is_312() {
  local python_exe="$1"
  [[ -x "$python_exe" ]] || return 1
  "$python_exe" - <<'PY' 2>/dev/null
import sys
raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)
PY
}

find_python312() {
  local candidate
  if [[ -n "${AIMEMO_PYTHON:-}" ]] && python_is_312 "$AIMEMO_PYTHON"; then
    echo "$AIMEMO_PYTHON"
    return 0
  fi
  for candidate in python3.12 python; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_312 "$(command -v "$candidate")"; then
      command -v "$candidate"
      return 0
    fi
  done
  for candidate in \
    "/opt/homebrew/opt/python@3.12/bin/python3.12" \
    "/usr/local/opt/python@3.12/bin/python3.12"; do
    if [[ "$(uname -s)" == "Darwin" ]] && python_is_312 "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    candidate="$(brew --prefix python@3.12 2>/dev/null || true)"
    if [[ -n "$candidate" ]] && python_is_312 "$candidate/bin/python3.12"; then
      echo "$candidate/bin/python3.12"
      return 0
    fi
  fi
  return 1
}

cd "$BACKEND_DIR"

if ! python_is_312 "$VENV_PYTHON"; then
  PYTHON312="$(find_python312 || true)"
  if [[ -z "$PYTHON312" ]]; then
    platform="$(uname -s)"
    install_hint=""
    case "$platform" in
      Darwin)
        install_hint=$'  macOS:         brew install python@3.12\n\nIf Python 3.12 is installed but not linked, add it to PATH or set AIMEMO_PYTHON:\n  export AIMEMO_PYTHON="/opt/homebrew/opt/python@3.12/bin/python3.12"'
        ;;
      Linux)
        install_hint=$'  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv\n  Fedora:        sudo dnf install python3.12'
        ;;
      *)
        install_hint=$'  Install Python 3.12 from your platform package manager, then rerun this script.'
        ;;
    esac
    cat >&2 <<'EOF'
Python 3.12 is required, but it was not found.

Install it first, then rerun this script. Examples:
EOF
    printf '%s\n' "$install_hint" >&2
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

echo "Starting AiMemo gateway at http://$HOST:$PORT ..."
echo "AiMemo app will be available at http://$HOST:$PORT/app after frontend build."
"$VENV_PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
