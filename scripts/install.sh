#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTER_SCRIPT="$SCRIPT_DIR/register-aimemo.sh"

if [[ ! -f "$REGISTER_SCRIPT" ]]; then
  echo "Expected registration script not found: $REGISTER_SCRIPT" >&2
  exit 1
fi

exec "$REGISTER_SCRIPT" "$@"
