#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

Starts AiMemo development services. If this checkout is already running,
start exits with a reminder to use aimemo restart.

Options:
  --skip-install  Skip dependency installation checks.
  --skip-doctor   Skip the doctor preflight.
  --no-desktop    Start backend/frontend only.
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
    run_script start-dev.sh "$@"
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
    run_script start-dev.sh "$@"
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
