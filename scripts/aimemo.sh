#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_LOG_DIR="$REPO_ROOT/data/dev_logs"
DEV_PID_FILE="$DEV_LOG_DIR/start-dev.pid"
DEV_STATUS_FILE="$DEV_LOG_DIR/start-dev.status"
COMMAND="${1:-help}"
if [[ "$#" -gt 0 ]]; then
  shift
fi

show_help() {
  cat <<'EOF'
Usage: aimemo <command> [args]

Commands:
  doctor    Run environment diagnostics.
  register  Register the global aimemo command.
  install   Alias for register.
  start     Start AiMemo dev services.
  restart   Stop and then start AiMemo dev services.
  stop      Stop AiMemo dev services.
  help      Show this help, or help for one command.

Planned commands:
  setup     Create minimal local config/directories. Not implemented yet.
  onboard   Run first-use configuration. Not implemented yet.

Examples:
  aimemo doctor
  aimemo start
  aimemo restart --no-desktop
  aimemo help register
EOF
}

show_command_help() {
  local topic="${1:-help}"
  case "$topic" in
    doctor)
      cat <<'EOF'
Usage: aimemo doctor [--json] [--fix] [--non-interactive] [--no-desktop]

Runs environment diagnostics for the current AiMemo checkout.

Options:
  --json             Print machine-readable JSON.
  --fix              Apply safe automatic fixes when supported.
  --non-interactive  Do not prompt for user input.
  --no-desktop       Skip desktop/Rust checks.
EOF
      ;;
    register|install)
      cat <<'EOF'
Usage: aimemo register [--dry-run] [--no-path-update] [--quiet]

Registers the global aimemo command for this checkout.

What it does on macOS/Linux:
  1. Writes a user-local wrapper to ~/.local/bin/aimemo by default.
  2. Adds that bin directory to the user's shell startup file unless
     --no-path-update is set.
  3. Lets new terminals run aimemo from any directory.

Options:
  --dry-run         Show the planned changes without writing files.
  --no-path-update  Write the wrapper but do not edit shell startup files.
  --quiet           Reduce output.

After registration:
  aimemo doctor
  aimemo start
  aimemo restart
  aimemo stop
EOF
      ;;
  start|dev)
      cat <<'EOF'
Usage: aimemo start [--skip-install] [--skip-doctor] [--no-desktop]

Starts AiMemo development services in the background. If this checkout is
already running, start exits with a reminder to use aimemo restart.

Options:
  --skip-install  Skip dependency installation checks.
  --skip-doctor   Skip the doctor preflight.
  --no-desktop    Start backend/frontend only.

Logs:
  data/dev_logs/start-dev.log

Start waits until backend and frontend are reachable, then prints the final
URLs. If a preferred port is busy, the printed URLs may use fallback ports.

For live foreground logs while debugging, run ./scripts/start-dev.sh directly.
EOF
      ;;
    restart)
      cat <<'EOF'
Usage: aimemo restart [--keep-windows] [--skip-install] [--skip-doctor] [--no-desktop]

Stops AiMemo development services for this checkout, then starts them again.

Options:
  --keep-windows  Leave terminal windows open when stopping.
  --skip-install  Skip dependency installation checks during start.
  --skip-doctor   Skip the doctor preflight during start.
  --no-desktop    Start backend/frontend only.
EOF
      ;;
    stop)
      cat <<'EOF'
Usage: aimemo stop [--keep-windows]

Stops AiMemo development services for this checkout.

Options:
  --keep-windows  Leave terminal windows open after stopping processes.
EOF
      ;;
    *)
      echo "Unknown help topic: $topic" >&2
      show_help >&2
      exit 2
      ;;
  esac
}

run_script() {
  local script_name="$1"
  shift
  local script_path="$SCRIPT_DIR/$script_name"
  if [[ ! -f "$script_path" ]]; then
    echo "Expected script not found: $script_path" >&2
    exit 1
  fi
  exec "$script_path" "$@"
}

aimemo_dev_process_running() {
  if [[ -f "$DEV_PID_FILE" ]]; then
    local pid
    pid="$(cat "$DEV_PID_FILE" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi

  local repo_escaped
  repo_escaped="$(printf '%s' "$REPO_ROOT" | sed 's/[.[\*^$()+?{}|\\]/\\&/g')"
  local pattern
  pattern="$repo_escaped.*(scripts/start-dev.sh|scripts/start-backend.sh|scripts/start-frontend.sh|uvicorn app.main:app|vite .*--host 127.0.0.1|npm run dev|tauri dev|memo-elf-desktop|memo-elf-native)"
  pgrep -f "$pattern" >/dev/null 2>&1
}

assert_aimemo_not_already_running() {
  if ! aimemo_dev_process_running; then
    return 0
  fi
  echo "AiMemo dev services already appear to be running for this checkout." >&2
  echo "Use 'aimemo stop' to stop them, or 'aimemo restart' to restart cleanly." >&2
  exit 2
}

status_value() {
  local key="$1"
  local file="$2"
  awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$file"
}

wait_for_start_ready() {
  local start_pid="$1"
  local log_file="$2"
  local timeout_seconds="${AIMEMO_START_WAIT_SECONDS:-300}"
  local elapsed=0

  echo "Waiting for AiMemo dev services to become ready..."
  while (( elapsed < timeout_seconds )); do
    if [[ -f "$DEV_STATUS_FILE" && "$(status_value STATUS "$DEV_STATUS_FILE")" == "ready" ]]; then
      local backend_url frontend_url product_url desktop_enabled desktop_skip_reason
      backend_url="$(status_value BACKEND_URL "$DEV_STATUS_FILE")"
      frontend_url="$(status_value FRONTEND_URL "$DEV_STATUS_FILE")"
      product_url="$(status_value PRODUCT_URL "$DEV_STATUS_FILE")"
      desktop_enabled="$(status_value DESKTOP_ENABLED "$DEV_STATUS_FILE")"
      desktop_skip_reason="$(status_value DESKTOP_SKIP_REASON "$DEV_STATUS_FILE")"

      echo "AiMemo dev services are ready."
      echo "Product:      $product_url"
      echo "Backend API:   $backend_url"
      echo "Frontend dev:  $frontend_url"
      if [[ "$desktop_enabled" == "1" ]]; then
        echo "Memo Elf:      enabled"
      elif [[ -n "$desktop_skip_reason" ]]; then
        echo "Memo Elf:      skipped ($desktop_skip_reason)"
      fi
      echo "Log:           $log_file"
      echo "Use 'aimemo stop' to stop services."
      return 0
    fi

    if ! kill -0 "$start_pid" 2>/dev/null; then
      rm -f "$DEV_PID_FILE"
      echo "AiMemo dev services failed to start. Recent log output:" >&2
      tail -n 120 "$log_file" >&2 || true
      exit 1
    fi

    sleep 1
    elapsed=$((elapsed + 1))
  done

  echo "AiMemo dev services are still starting after ${timeout_seconds}s." >&2
  echo "Log: $log_file" >&2
  echo "Use 'aimemo stop' to stop the background startup if needed." >&2
  exit 1
}

start_dev_background() {
  local log_file="$DEV_LOG_DIR/start-dev.log"
  mkdir -p "$DEV_LOG_DIR"
  assert_aimemo_not_already_running
  rm -f "$DEV_STATUS_FILE"

  echo "Starting AiMemo dev services in the background..."
  echo "Log: $log_file"

  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$SCRIPT_DIR/start-dev.sh" "$@" >"$log_file" 2>&1 &
  else
    nohup "$SCRIPT_DIR/start-dev.sh" "$@" >"$log_file" 2>&1 &
  fi
  local start_pid=$!
  printf '%s\n' "$start_pid" >"$DEV_PID_FILE"

  sleep 1
  if ! kill -0 "$start_pid" 2>/dev/null; then
    rm -f "$DEV_PID_FILE"
    echo "AiMemo dev services failed to start. Recent log output:" >&2
    tail -n 80 "$log_file" >&2 || true
    exit 1
  fi

  wait_for_start_ready "$start_pid" "$log_file"
}

case "$COMMAND" in
  doctor)
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
      show_command_help doctor
      exit 0
    fi
    run_script doctor.sh "$@"
    ;;
  start|dev)
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
      show_command_help start
      exit 0
    fi
    start_dev_background "$@"
    ;;
  stop)
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
      show_command_help stop
      exit 0
    fi
    run_script stop-dev.sh "$@"
    ;;
  restart)
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
      show_command_help restart
      exit 0
    fi
    "$SCRIPT_DIR/stop-dev.sh"
    start_dev_background "$@"
    ;;
  register)
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
      show_command_help register
      exit 0
    fi
    run_script register-aimemo.sh "$@"
    ;;
  install)
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
      show_command_help install
      exit 0
    fi
    run_script install.sh "$@"
    ;;
  help|-h|--help)
    if [[ "$#" -gt 0 ]]; then
      show_command_help "$1"
    else
      show_help
    fi
    ;;
  setup)
    echo "aimemo setup is planned but not implemented yet. Create .env from .env.example manually for now." >&2
    exit 2
    ;;
  onboard)
    echo "aimemo onboard is planned but not implemented yet. Configure .env manually for now." >&2
    exit 2
    ;;
  *)
    echo "Unknown aimemo command: $COMMAND" >&2
    show_help >&2
    exit 2
    ;;
esac
