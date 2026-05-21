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

warn_linux_file_watch_limit() {
  [[ "$(uname -s)" == "Linux" ]] || return 0

  local max_watches max_instances
  max_watches="$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)"
  max_instances="$(cat /proc/sys/fs/inotify/max_user_instances 2>/dev/null || echo 0)"

  if (( max_watches < 262144 || max_instances < 512 )); then
    cat >&2 <<EOF
Warning: Linux file watch limits look low for Vite + Tauri dev.
Current: fs.inotify.max_user_watches=$max_watches, fs.inotify.max_user_instances=$max_instances
If startup fails with ENOSPC / OS file watch limit reached, run:
  sudo sysctl -w fs.inotify.max_user_watches=524288 fs.inotify.max_user_instances=1024

To make it persistent:
  printf 'fs.inotify.max_user_watches=524288\nfs.inotify.max_user_instances=1024\n' | sudo tee /etc/sysctl.d/99-aimemo-dev.conf
  sudo sysctl --system

EOF
  fi
}

warn_invalid_proxy_scheme() {
  local proxy_var proxy_value
  for proxy_var in ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy; do
    proxy_value="${!proxy_var:-}"
    if [[ "$proxy_value" == socks://* ]]; then
      cat >&2 <<EOF
Warning: $proxy_var uses unsupported proxy scheme: $proxy_value
Use socks5:// instead, or unset it and use HTTP_PROXY/HTTPS_PROXY.
Example:
  export $proxy_var="${proxy_value/socks:\/\//socks5://}"

EOF
    fi
  done
}

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

ensure_frontend_dist_for_backend_app() {
  local frontend_dir="$REPO_ROOT/frontend"
  local index_html="$frontend_dir/dist/index.html"

  if [[ -f "$index_html" ]]; then
    return 0
  fi

  echo "Building frontend once for backend-hosted /app entry..."
  (
    cd "$frontend_dir"
    if [[ "$SKIP_INSTALL" -eq 0 || ! -d "node_modules" ]]; then
      npm install
    fi
    npm run build
  )
}

"$SCRIPT_DIR/stop-dev.sh"
warn_linux_file_watch_limit
warn_invalid_proxy_scheme
ensure_frontend_dist_for_backend_app

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
    if [[ "$(uname -s)" != "Linux" && ( "$SKIP_INSTALL" -eq 0 || ! -d "node_modules" ) ]]; then
      npm install
    fi
    if [[ "$(uname -s)" == "Linux" ]]; then
      (
        cd "$REPO_ROOT/desktop/src-tauri"
        cargo run --bin memo-elf-native
      )
    else
      npm run dev
    fi
  ) &
  DESKTOP_PID=$!
fi

if [[ "$NO_DESKTOP" -eq 0 ]]; then
  wait "$BACKEND_PID" "$FRONTEND_PID" "$DESKTOP_PID"
else
  wait "$BACKEND_PID" "$FRONTEND_PID"
fi
