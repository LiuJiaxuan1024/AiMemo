#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DESKTOP_EXE="$REPO_ROOT/desktop/src-tauri/target/debug/memo-elf-desktop"

stop_port_processes() {
  local port="$1"
  local name="$2"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Stopping $name on port $port: $pids"
    kill $pids 2>/dev/null || true
  fi
}

stop_matching_processes() {
  local pattern="$1"
  local name="$2"
  local pids
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Stopping $name: $pids"
    kill $pids 2>/dev/null || true
  fi
}

stop_matching_processes "$DESKTOP_EXE" "Memo Elf desktop"
stop_matching_processes "$REPO_ROOT.*npm run dev" "AiMemo npm dev processes"
stop_matching_processes "$REPO_ROOT.*uvicorn app.main:app" "AiMemo backend processes"
stop_matching_processes "$REPO_ROOT.*vite.*--host 127.0.0.1" "AiMemo Vite dev processes"

echo "AiMemo dev services stopped."
