#!/usr/bin/env bash
set -euo pipefail

SKIP_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --skip-install)
      SKIP_INSTALL=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./scripts/start-frontend.sh [--skip-install]" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"

cd "$FRONTEND_DIR"

if [[ "$SKIP_INSTALL" -eq 0 || ! -d "node_modules" ]]; then
  echo "Installing frontend dependencies..."
  npm install
fi

echo "Starting AiMemo frontend dev server at http://127.0.0.1:5173/app/ ..."
echo "Product entry remains http://127.0.0.1:8000/app after frontend build."
npm run dev -- --host 127.0.0.1
