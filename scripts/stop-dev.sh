#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_PID_FILE="$REPO_ROOT/data/dev_logs/start-dev.pid"
DEV_STATUS_FILE="$REPO_ROOT/data/dev_logs/start-dev.status"
DESKTOP_EXE="$REPO_ROOT/desktop/src-tauri/target/debug/memo-elf-desktop"
NATIVE_ELF_EXE="$REPO_ROOT/desktop/src-tauri/target/debug/memo-elf-native"

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
    for _ in 1 2 3 4 5; do
      sleep 0.2
      if ! pgrep -f "$pattern" >/dev/null 2>&1; then
        return 0
      fi
    done
  fi
}

stop_recorded_process_group() {
  if [[ ! -f "$DEV_PID_FILE" ]]; then
    return 0
  fi

  local pid
  pid="$(cat "$DEV_PID_FILE" 2>/dev/null || true)"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$DEV_PID_FILE" "$DEV_STATUS_FILE"
    return 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$DEV_PID_FILE" "$DEV_STATUS_FILE"
    return 0
  fi

  echo "Stopping AiMemo dev process group: $pid"
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.2
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$DEV_PID_FILE" "$DEV_STATUS_FILE"
      return 0
    fi
  done

  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  rm -f "$DEV_PID_FILE" "$DEV_STATUS_FILE"
}

stop_recorded_process_group
stop_matching_processes "$DESKTOP_EXE" "Memo Elf desktop"
stop_matching_processes "$NATIVE_ELF_EXE" "Memo Elf native desktop"
stop_matching_processes "$REPO_ROOT/desktop/src-tauri.*cargo run --bin memo-elf-native" "Memo Elf native cargo runner"
stop_matching_processes "$REPO_ROOT/scripts/start-backend.sh" "AiMemo backend starter"
stop_matching_processes "$REPO_ROOT/scripts/start-frontend.sh" "AiMemo frontend starter"
stop_matching_processes "$REPO_ROOT/backend/.venv/bin/python .*pip.*install" "AiMemo backend dependency installer"
stop_matching_processes "$REPO_ROOT.*npm run dev" "AiMemo npm dev processes"
stop_matching_processes "$REPO_ROOT.*uvicorn app.main:app" "AiMemo backend processes"
stop_matching_processes "$REPO_ROOT.*vite.*--host 127.0.0.1" "AiMemo Vite dev processes"
stop_matching_processes "$REPO_ROOT/scripts/start-dev.sh" "AiMemo start-dev supervisor"
rm -f "$DEV_PID_FILE" "$DEV_STATUS_FILE"

echo "AiMemo dev services stopped."
