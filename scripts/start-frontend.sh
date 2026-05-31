#!/usr/bin/env bash
set -euo pipefail

SKIP_INSTALL=0
HOST="${AIMEMO_HOST:-127.0.0.1}"
PORT="${AIMEMO_FRONTEND_PORT:-5173}"
BACKEND_PORT="${AIMEMO_BACKEND_PORT:-8000}"
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
    --backend-port=*)
      BACKEND_PORT="${arg#--backend-port=}"
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./scripts/start-frontend.sh [--skip-install] [--host=127.0.0.1] [--port=5173] [--backend-port=8000]" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
source "$SCRIPT_DIR/dev-utils.sh"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required. Install Node.js 20+ from https://nodejs.org/ and make sure npm is in PATH." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "node is required. Install Node.js 20+ from https://nodejs.org/ and make sure node is in PATH." >&2
  exit 1
fi
NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if (( NODE_MAJOR < 20 )); then
  echo "Node.js 20+ is required. Current version: $(node --version). Install Node.js 20+ from https://nodejs.org/." >&2
  exit 1
fi

ACTUAL_PORT="$(find_available_port "$HOST" "$PORT")"
print_port_fallback "Frontend" "$PORT" "$ACTUAL_PORT"
PORT="$ACTUAL_PORT"

cd "$FRONTEND_DIR"

package_installed() {
  local package_name="$1"
  [[ -d "node_modules" ]] || return 1
  npm ls "$package_name" --depth=0 --silent >/dev/null 2>&1
}

if [[ "$SKIP_INSTALL" -eq 0 || ! -d "node_modules" ]] || ! package_installed mermaid; then
  echo "Installing frontend dependencies..."
  npm install
fi

if ! package_installed mermaid; then
  echo "Frontend dependency 'mermaid' is missing. Run 'npm install' in frontend/ or rerun without --skip-install." >&2
  exit 1
fi

echo "Starting AiMemo frontend dev server at http://$HOST:$PORT/app/ ..."
echo "Product entry remains http://$HOST:$BACKEND_PORT/app after frontend build."
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://$HOST:$BACKEND_PORT}" npm run dev -- --host "$HOST" --port "$PORT" --strictPort
