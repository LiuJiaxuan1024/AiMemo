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
  stop      Stop AiMemo dev services.
  help      Show this help.

Planned commands:
  setup     Create minimal local config/directories. Not implemented yet.
  onboard   Run first-use configuration. Not implemented yet.
EOF
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
    run_script doctor.sh "$@"
    ;;
  start|dev)
    run_script start-dev.sh "$@"
    ;;
  stop)
    run_script stop-dev.sh "$@"
    ;;
  register)
    run_script register-aimemo.sh "$@"
    ;;
  install)
    run_script install.sh "$@"
    ;;
  help|-h|--help)
    show_help
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
