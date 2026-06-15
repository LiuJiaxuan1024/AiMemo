#!/usr/bin/env bash
set -euo pipefail

JSON=0
FIX=0
NON_INTERACTIVE=0
NO_DESKTOP=0

for arg in "$@"; do
  case "$arg" in
    --json)
      JSON=1
      ;;
    --fix)
      FIX=1
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      ;;
    --no-desktop)
      NO_DESKTOP=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./scripts/doctor.sh [--json] [--fix] [--non-interactive] [--no-desktop]" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"

CHECK_IDS=()
CHECK_STATUSES=()
CHECK_MESSAGES=()
CHECK_HINTS=()

add_check() {
  CHECK_IDS+=("$1")
  CHECK_STATUSES+=("$2")
  CHECK_MESSAGES+=("$3")
  CHECK_HINTS+=("${4:-}")
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/}"
  printf '%s' "$value"
}

python312_available() {
  local exe="$1"
  shift || true
  command -v "$exe" >/dev/null 2>&1 || [[ -x "$exe" ]] || return 1
  "$exe" "$@" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1
}

find_python312() {
  if [[ -n "${AIMEMO_PYTHON:-}" ]] && python312_available "$AIMEMO_PYTHON"; then
    return 0
  fi
  python312_available python3.12 && return 0
  python312_available python && return 0
  return 1
}

port_available() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" >/dev/null 2>&1 <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

frontend_package_installed() {
  local package_name="$1"
  (
    cd "$FRONTEND_DIR"
    [[ -d node_modules ]] || exit 1
    npm ls "$package_name" --depth=0 --silent >/dev/null 2>&1
  )
}

if [[ "$FIX" -eq 1 ]]; then
  add_check "fix" "warn" "doctor --fix is not implemented in Phase 1." "Run existing start scripts to let them install dependencies, or implement Phase 2 repair actions."
fi

if [[ -d "$REPO_ROOT/.git" ]]; then
  add_check "repo.git" "ok" "Git repository detected."
else
  add_check "repo.git" "warn" "This directory does not look like a Git checkout." "Run doctor from the AiMemo repository root."
fi

if command -v git >/dev/null 2>&1; then
  add_check "git.command" "ok" "git is available."
else
  add_check "git.command" "error" "git was not found on PATH." "Install Git and reopen your shell."
fi

EXPECTED_AIMEMO_BIN_DIR="${AIMEMO_BIN_DIR:-${HOME:-}/.local/bin}"
EXPECTED_AIMEMO_WRAPPER="$EXPECTED_AIMEMO_BIN_DIR/aimemo"
EXPECTED_AIMEMO_SCRIPT="$SCRIPT_DIR/aimemo.sh"
if aimemo_path="$(command -v aimemo 2>/dev/null)"; then
  resolved_aimemo_path="$(cd "$(dirname "$aimemo_path")" 2>/dev/null && pwd -P)/$(basename "$aimemo_path")"
  if [[ -d "$EXPECTED_AIMEMO_BIN_DIR" ]]; then
    resolved_expected_wrapper="$(cd "$EXPECTED_AIMEMO_BIN_DIR" 2>/dev/null && pwd -P)/aimemo"
  else
    resolved_expected_wrapper="$EXPECTED_AIMEMO_WRAPPER"
  fi
  if [[ "$resolved_aimemo_path" == "$resolved_expected_wrapper" ]]; then
    wrapper_content="$(cat "$EXPECTED_AIMEMO_WRAPPER" 2>/dev/null || true)"
    if [[ "$wrapper_content" == *"$EXPECTED_AIMEMO_SCRIPT"* ]]; then
      add_check "aimemo.global" "ok" "Global aimemo command is registered for this checkout."
    else
      add_check "aimemo.global" "warn" "Global aimemo command exists but points to a different checkout." "Run ./scripts/register-aimemo.sh to refresh the wrapper."
    fi
  else
    add_check "aimemo.global" "warn" "An aimemo command exists, but it is not this checkout's wrapper." "Found: $aimemo_path. Run ./scripts/register-aimemo.sh to refresh the user-local wrapper."
  fi
elif [[ -f "$EXPECTED_AIMEMO_WRAPPER" ]]; then
  add_check "aimemo.global" "warn" "Global aimemo wrapper exists but is not on PATH." "Run ./scripts/register-aimemo.sh, then restart your shell if needed."
else
  add_check "aimemo.global" "warn" "Global aimemo command is not registered." "Run ./scripts/register-aimemo.sh to enable commands like aimemo doctor."
fi

if command -v node >/dev/null 2>&1; then
  node_version="$(node --version 2>/dev/null || true)"
  node_major="$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)"
  if (( node_major >= 20 )); then
    add_check "node.version" "ok" "Node.js is available: $node_version."
  else
    add_check "node.version" "error" "Node.js 20+ is required. Current version: $node_version." "Install Node.js 20+."
  fi
else
  add_check "node.version" "error" "node was not found on PATH." "Install Node.js 20+."
fi

if command -v npm >/dev/null 2>&1; then
  add_check "npm.command" "ok" "npm is available."
else
  add_check "npm.command" "error" "npm was not found on PATH." "Install Node.js 20+ with npm."
fi

VENV_READY=0
if [[ -x "$VENV_PYTHON" ]] && python312_available "$VENV_PYTHON"; then
  VENV_READY=1
fi

if find_python312; then
  add_check "python.312" "ok" "Python 3.12 is available."
elif [[ "$VENV_READY" -eq 1 ]]; then
  add_check "python.312" "warn" "Standalone Python 3.12 was not found, but backend/.venv is usable." "Install Python 3.12 or set AIMEMO_PYTHON before rebuilding backend/.venv."
else
  add_check "python.312" "error" "Python 3.12 was not found." "Install Python 3.12 or set AIMEMO_PYTHON to a Python 3.12 executable."
fi

if [[ "$VENV_READY" -eq 1 ]]; then
  add_check "backend.venv" "ok" "backend/.venv uses Python 3.12."
elif [[ -d "$BACKEND_DIR/.venv" ]]; then
  add_check "backend.venv" "error" "backend/.venv exists but is not Python 3.12." "Run scripts/start-backend.sh or Phase 2 doctor --fix to rebuild it."
else
  add_check "backend.venv" "error" "backend/.venv is missing." "Run scripts/start-backend.sh or Phase 2 doctor --fix to create it."
fi

if [[ "$VENV_READY" -eq 1 ]]; then
  if (cd "$BACKEND_DIR" && "$VENV_PYTHON" -c 'import fastapi, sqlmodel, langgraph; import app.main' >/dev/null 2>&1); then
    add_check "backend.imports" "ok" "Backend core imports are available."
  else
    add_check "backend.imports" "error" "Backend core imports failed." "Install backend dependencies with scripts/start-backend.sh."
  fi
else
  add_check "backend.imports" "skip" "Skipped backend import check because backend/.venv is not ready."
fi

if [[ -f "$REPO_ROOT/.env" ]]; then
  add_check "config.env" "ok" ".env exists."
else
  add_check "config.env" "warn" ".env is missing." "Copy .env.example to .env and fill required API keys."
fi

if [[ -n "${DASHSCOPE_API_KEY:-}" ]]; then
  add_check "config.dashscope" "ok" "DASHSCOPE_API_KEY is present in the current process environment."
else
  add_check "config.dashscope" "warn" "DASHSCOPE_API_KEY is not present in the current process environment." "If it is stored in .env, backend startup will load it; onboard should make this explicit later."
fi

if [[ -d "$FRONTEND_DIR/node_modules" ]]; then
  add_check "frontend.node_modules" "ok" "frontend/node_modules exists."
else
  add_check "frontend.node_modules" "error" "frontend/node_modules is missing." "Run npm install in frontend/ or Phase 2 doctor --fix."
fi

if command -v npm >/dev/null 2>&1; then
  if frontend_package_installed mermaid; then
    add_check "frontend.mermaid" "ok" "Frontend dependency mermaid is installed."
  else
    add_check "frontend.mermaid" "error" "Frontend dependency mermaid is missing." "Run npm install in frontend/."
  fi
else
  add_check "frontend.mermaid" "skip" "Skipped frontend package check because npm is missing."
fi

if [[ -f "$FRONTEND_DIR/dist/index.html" ]]; then
  add_check "frontend.dist" "ok" "frontend/dist/index.html exists."
else
  add_check "frontend.dist" "warn" "frontend/dist/index.html is missing." "Run npm run build in frontend/ before using the backend-hosted /app entry."
fi

HOST="${AIMEMO_HOST:-127.0.0.1}"
if port_available "$HOST" "${AIMEMO_BACKEND_PORT:-8000}"; then
  add_check "port.backend" "ok" "Backend port ${AIMEMO_BACKEND_PORT:-8000} is available."
else
  add_check "port.backend" "warn" "Backend port ${AIMEMO_BACKEND_PORT:-8000} is already in use." "start-dev can choose a fallback port; stop old services if this is unexpected."
fi

if port_available "$HOST" "${AIMEMO_FRONTEND_PORT:-5173}"; then
  add_check "port.frontend" "ok" "Frontend port ${AIMEMO_FRONTEND_PORT:-5173} is available."
else
  add_check "port.frontend" "warn" "Frontend port ${AIMEMO_FRONTEND_PORT:-5173} is already in use." "start-dev can choose a fallback port; stop old services if this is unexpected."
fi

if [[ "$NO_DESKTOP" -eq 1 ]]; then
  add_check "desktop.rust" "skip" "Desktop checks skipped by --no-desktop."
elif command -v cargo >/dev/null 2>&1; then
  add_check "desktop.rust" "ok" "Rust/Cargo is available."
else
  add_check "desktop.rust" "warn" "Rust/Cargo was not found." "Web startup can continue. Install Rust only if you need Memo Elf desktop."
fi

errors=0
warnings=0
for status in "${CHECK_STATUSES[@]}"; do
  [[ "$status" == "error" ]] && ((errors += 1))
  [[ "$status" == "warn" ]] && ((warnings += 1))
done

if [[ "$JSON" -eq 1 ]]; then
  printf '{"ok":%s,"errors":%d,"warnings":%d,"checks":[' "$([[ "$errors" -eq 0 ]] && echo true || echo false)" "$errors" "$warnings"
  for index in "${!CHECK_IDS[@]}"; do
    if [[ "$index" -gt 0 ]]; then
      printf ','
    fi
    printf '{"id":"%s","status":"%s","message":"%s","hint":"%s"}' \
      "$(json_escape "${CHECK_IDS[$index]}")" \
      "$(json_escape "${CHECK_STATUSES[$index]}")" \
      "$(json_escape "${CHECK_MESSAGES[$index]}")" \
      "$(json_escape "${CHECK_HINTS[$index]}")"
  done
  printf ']}\n'
else
  echo "AiMemo doctor"
  for index in "${!CHECK_IDS[@]}"; do
    case "${CHECK_STATUSES[$index]}" in
      ok) prefix="[OK]" ;;
      warn) prefix="[WARN]" ;;
      error) prefix="[ERROR]" ;;
      skip) prefix="[SKIP]" ;;
    esac
    echo "$prefix ${CHECK_IDS[$index]}: ${CHECK_MESSAGES[$index]}"
    if [[ -n "${CHECK_HINTS[$index]}" ]]; then
      echo "       ${CHECK_HINTS[$index]}"
    fi
  done
  echo "Summary: $errors error(s), $warnings warning(s)."
fi

if [[ "$errors" -gt 0 ]]; then
  exit 1
fi
