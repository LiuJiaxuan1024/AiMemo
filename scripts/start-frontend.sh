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

ACTUAL_PORT="$(find_available_port "$HOST" "$PORT")"
print_port_fallback "Frontend" "$PORT" "$ACTUAL_PORT"
PORT="$ACTUAL_PORT"

cd "$FRONTEND_DIR"

if [[ "$SKIP_INSTALL" -eq 0 || ! -d "node_modules" ]]; then
  echo "Installing frontend dependencies..."
  npm install
fi

echo "Starting AiMemo frontend dev server at http://$HOST:$PORT/app/ ..."
echo "Product entry remains http://$HOST:$BACKEND_PORT/app after frontend build."
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://$HOST:$BACKEND_PORT}" npm run dev -- --host "$HOST" --port "$PORT" --strictPort
