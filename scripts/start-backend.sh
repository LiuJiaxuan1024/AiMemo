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
  local candidate
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi
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
    platform="$(uname -s)"
    install_hint=""
    case "$platform" in
      Darwin)
        install_hint=$'  macOS:         brew install python@3.12\n\nIf Python 3.12 is installed but not linked, add it to PATH or use the Homebrew\npath directly:\n  export PATH="/opt/homebrew/opt/python@3.12/bin:$PATH"'
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

echo "Starting AiMemo gateway at http://127.0.0.1:8000 ..."
echo "AiMemo app will be available at http://127.0.0.1:8000/app after frontend build."
"$VENV_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
