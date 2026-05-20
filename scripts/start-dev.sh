#!/usr/bin/env bash
set -euo pipefail

SKIP_INSTALL=0
NO_DESKTOP=0
for arg in "$@"; do
  case "$arg" in
    --skip-install)
      SKIP_INSTALL=1
      ;;
    --no-desktop)
      NO_DESKTOP=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./scripts/start-dev.sh [--skip-install] [--no-desktop]" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

stop_port_processes() {
  local port="$1"
  local name="$2"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Stopping existing $name process on port $port: $pids"
    kill $pids 2>/dev/null || true
  fi
}

"$SCRIPT_DIR/stop-dev.sh"

ARGS=()
if [[ "$SKIP_INSTALL" -eq 1 ]]; then
  ARGS+=(--skip-install)
fi

echo "Starting AiMemo backend, frontend, and Memo Elf..."
echo "Backend:  http://127.0.0.1:8000"
echo "Frontend: http://127.0.0.1:5173/app/"
if [[ "$NO_DESKTOP" -eq 0 ]]; then
  echo "Memo Elf: Tauri desktop window"
fi

"$SCRIPT_DIR/start-backend.sh" "${ARGS[@]}" &
BACKEND_PID=$!

cleanup() {
  echo "Stopping AiMemo dev services..."
  kill "$BACKEND_PID" "$FRONTEND_PID" "${DESKTOP_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

sleep 2
"$SCRIPT_DIR/start-frontend.sh" "${ARGS[@]}" &
FRONTEND_PID=$!

if [[ "$NO_DESKTOP" -eq 0 ]]; then
  (
    cd "$REPO_ROOT/desktop"
    if [[ "$SKIP_INSTALL" -eq 0 || ! -d "node_modules" ]]; then
      npm install
    fi
    npm run dev
  ) &
  DESKTOP_PID=$!
fi

if [[ "$NO_DESKTOP" -eq 0 ]]; then
  wait "$BACKEND_PID" "$FRONTEND_PID" "$DESKTOP_PID"
else
  wait "$BACKEND_PID" "$FRONTEND_PID"
fi
