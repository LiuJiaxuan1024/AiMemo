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
source "$SCRIPT_DIR/dev-utils.sh"

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
  local stale_marker=""

  # Linux 桌宠和后端统一入口会打开后端实际端口上的 /app，
  # 这个入口读取的是 frontend/dist，而不是 Vite 的 5173 开发产物。
  # 因此开发脚本需要在源码更新后刷新 dist，避免用户看到旧版前端。
  if [[ -f "$index_html" ]]; then
    stale_marker="$(
      find \
        "$frontend_dir/src" \
        "$frontend_dir/public" \
        "$frontend_dir/index.html" \
        "$frontend_dir/package.json" \
        "$frontend_dir/package-lock.json" \
        "$frontend_dir/vite.config.ts" \
        "$frontend_dir/tsconfig.json" \
        "$frontend_dir/tsconfig.app.json" \
        -newer "$index_html" \
        -print \
        -quit 2>/dev/null || true
    )"
  fi

  if [[ -f "$index_html" && -z "$stale_marker" ]]; then
    return 0
  fi

  if [[ -n "$stale_marker" ]]; then
    echo "Frontend dist is stale because this file changed after the last build:"
    echo "  $stale_marker"
  fi
  echo "Building frontend for backend-hosted /app entry..."
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

HOST="${AIMEMO_HOST:-127.0.0.1}"
PREFERRED_BACKEND_PORT="${AIMEMO_BACKEND_PORT:-8000}"
PREFERRED_FRONTEND_PORT="${AIMEMO_FRONTEND_PORT:-5173}"
PREFERRED_DESKTOP_PORT="${AIMEMO_DESKTOP_PORT:-1420}"
BACKEND_PORT="$(find_available_port "$HOST" "$PREFERRED_BACKEND_PORT")"
FRONTEND_PORT="$(find_available_port "$HOST" "$PREFERRED_FRONTEND_PORT")"
DESKTOP_PORT="$(find_available_port "$HOST" "$PREFERRED_DESKTOP_PORT")"
export AIMEMO_HOST="$HOST"
export AIMEMO_BACKEND_PORT="$BACKEND_PORT"
export AIMEMO_FRONTEND_PORT="$FRONTEND_PORT"
export AIMEMO_DESKTOP_PORT="$DESKTOP_PORT"
export AIMEMO_BACKEND_URL="${AIMEMO_BACKEND_URL:-http://$HOST:$BACKEND_PORT}"
export VITE_API_BASE_URL="${VITE_API_BASE_URL:-$AIMEMO_BACKEND_URL}"
export VITE_AIMEMO_BACKEND_URL="${VITE_AIMEMO_BACKEND_URL:-$AIMEMO_BACKEND_URL}"

START_ARGS=""
if [[ "$SKIP_INSTALL" -eq 1 ]]; then
  START_ARGS="--skip-install"
fi

echo "Starting AiMemo backend, frontend, and Memo Elf..."
print_port_fallback "Backend" "$PREFERRED_BACKEND_PORT" "$BACKEND_PORT"
print_port_fallback "Frontend" "$PREFERRED_FRONTEND_PORT" "$FRONTEND_PORT"
if [[ "$NO_DESKTOP" -eq 0 && "$(uname -s)" != "Linux" ]]; then
  print_port_fallback "Memo Elf webview" "$PREFERRED_DESKTOP_PORT" "$DESKTOP_PORT"
fi
echo "Backend:  http://$HOST:$BACKEND_PORT"
echo "Frontend: http://$HOST:$FRONTEND_PORT/app/"
echo "Product:  http://$HOST:$BACKEND_PORT/app/"
if [[ "$NO_DESKTOP" -eq 0 ]]; then
  echo "Memo Elf: Tauri desktop window"
fi

"$SCRIPT_DIR/start-backend.sh" $START_ARGS --host="$HOST" --port="$BACKEND_PORT" &
BACKEND_PID=$!

cleanup() {
  echo "Stopping AiMemo dev services..."
  kill "$BACKEND_PID" "$FRONTEND_PID" "${DESKTOP_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

sleep 2
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  wait "$BACKEND_PID" || true
  echo "AiMemo backend failed to start. Fix the backend error above, then rerun ./scripts/start-dev.sh." >&2
  exit 1
fi
"$SCRIPT_DIR/start-frontend.sh" $START_ARGS --host="$HOST" --port="$FRONTEND_PORT" --backend-port="$BACKEND_PORT" &
FRONTEND_PID=$!

if [[ "$NO_DESKTOP" -eq 0 ]]; then
  (
    cd "$REPO_ROOT/desktop"
    if [[ "$(uname -s)" != "Linux" ]] && [[ "$SKIP_INSTALL" -eq 0 || ! -d "node_modules" ]]; then
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
