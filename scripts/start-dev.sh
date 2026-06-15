#!/usr/bin/env bash
set -euo pipefail

SKIP_INSTALL=0
NO_DESKTOP=0
SKIP_DOCTOR=0
DESKTOP_SKIP_REASON=""
for arg in "$@"; do
  case "$arg" in
    --skip-install)
      SKIP_INSTALL=1
      ;;
    --no-desktop)
      NO_DESKTOP=1
      ;;
    --skip-doctor)
      SKIP_DOCTOR=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./scripts/start-dev.sh [--skip-install] [--no-desktop] [--skip-doctor]" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_LOG_DIR="$REPO_ROOT/data/dev_logs"
DEV_PID_FILE="$DEV_LOG_DIR/start-dev.pid"
DEV_STATUS_FILE="$DEV_LOG_DIR/start-dev.status"
source "$SCRIPT_DIR/dev-utils.sh"
mkdir -p "$DEV_LOG_DIR"
rm -f "$DEV_STATUS_FILE"

assert_command_available() {
  local command_name="$1"
  local install_hint="$2"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required. $install_hint" >&2
    exit 1
  fi
}

assert_node_version() {
  assert_command_available node "Install Node.js 20+ from https://nodejs.org/ and make sure node is in PATH."
  assert_command_available npm "Install Node.js 20+ from https://nodejs.org/ and make sure npm is in PATH."

  local node_major
  node_major="$(node -p "process.versions.node.split('.')[0]")"
  if (( node_major < 20 )); then
    echo "Node.js 20+ is required. Current version: $(node --version). Install Node.js 20+ from https://nodejs.org/." >&2
    exit 1
  fi
}

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

frontend_package_installed() {
  local package_name="$1"
  (
    cd "$REPO_ROOT/frontend"
    [[ -d "node_modules" ]] || exit 1
    npm ls "$package_name" --depth=0 --silent >/dev/null 2>&1
  )
}

ensure_frontend_dependencies() {
  local frontend_dir="$REPO_ROOT/frontend"
  if [[ "$SKIP_INSTALL" -eq 0 || ! -d "$frontend_dir/node_modules" ]] || ! frontend_package_installed mermaid; then
    (
      cd "$frontend_dir"
      echo "Installing frontend dependencies..."
      npm install
    )
  fi

  if ! frontend_package_installed mermaid; then
    echo "Frontend dependency 'mermaid' is missing. Run 'npm install' in frontend/ or rerun without --skip-install." >&2
    exit 1
  fi
}

ensure_desktop_dependencies() {
  if [[ "$NO_DESKTOP" -eq 1 ]]; then
    DESKTOP_SKIP_REASON="disabled by --no-desktop"
    return 1
  fi

  if [[ "$(read_project_config_value "elf.enabled" "true")" != "true" ]]; then
    DESKTOP_SKIP_REASON="disabled by config.json5 elf.enabled=false"
    return 1
  fi

  if ! command -v cargo >/dev/null 2>&1; then
    echo "Warning: Rust/Cargo was not found. Skipping Memo Elf desktop window." >&2
    echo "Install Rust from https://rustup.rs/ and rerun without --no-desktop to enable it." >&2
    DESKTOP_SKIP_REASON="Rust/Cargo is not installed"
    return 1
  fi

  if [[ "$(uname -s)" != "Linux" && ( "$SKIP_INSTALL" -eq 0 || ! -d "$REPO_ROOT/desktop/node_modules" ) ]]; then
    (
      cd "$REPO_ROOT/desktop"
      echo "Installing desktop dependencies..."
      npm install
    )
  fi

  return 0
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

aimemo_dev_process_running() {
  local repo_escaped
  repo_escaped="$(printf '%s' "$REPO_ROOT" | sed 's/[.[\*^$()+?{}|\\]/\\&/g')"
  local pattern
  pattern="$repo_escaped.*(uvicorn app.main:app|scripts/start-backend.sh|scripts/start-frontend.sh|vite .*--host 127.0.0.1|npm run dev|tauri dev|memo-elf-desktop|memo-elf-native)"
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
    ensure_frontend_dependencies
    npm run build
  )
}

run_quick_doctor() {
  if [[ "$SKIP_DOCTOR" -eq 1 || ! -f "$SCRIPT_DIR/doctor.sh" ]]; then
    return 0
  fi

  echo "Running AiMemo doctor quick check..."
  local doctor_args=(--non-interactive)
  if [[ "$NO_DESKTOP" -eq 1 ]]; then
    doctor_args+=(--no-desktop)
  fi

  if ! "$SCRIPT_DIR/doctor.sh" "${doctor_args[@]}"; then
    echo "Warning: AiMemo doctor reported issues. start-dev will continue with the current compatibility startup path." >&2
    echo "For a focused report, run: ./scripts/doctor.sh" >&2
  fi
}

wait_for_http() {
  local name="$1"
  local url="$2"
  local timeout_seconds="${3:-60}"

  node - "$name" "$url" "$timeout_seconds" <<'JS'
const http = require("node:http");
const https = require("node:https");

const name = process.argv[2];
const url = process.argv[3];
const timeoutMs = Number(process.argv[4]) * 1000;
const deadline = Date.now() + timeoutMs;
const client = url.startsWith("https:") ? https : http;

function tryOnce() {
  const req = client.get(url, { timeout: 1000 }, (res) => {
    res.resume();
    if (res.statusCode >= 200 && res.statusCode < 500) {
      process.exit(0);
    }
    retry();
  });

  req.on("timeout", () => {
    req.destroy();
    retry();
  });
  req.on("error", retry);
}

function retry() {
  if (Date.now() >= deadline) {
    console.error(`${name} did not become ready before timeout: ${url}`);
    process.exit(1);
  }
  setTimeout(tryOnce, 500);
}

tryOnce();
JS
}

write_start_status() {
  local status="$1"
  mkdir -p "$DEV_LOG_DIR"
  cat >"$DEV_STATUS_FILE" <<EOF
STATUS=$status
BACKEND_URL=http://$HOST:$BACKEND_PORT
FRONTEND_URL=http://$HOST:$FRONTEND_PORT/app/
PRODUCT_URL=http://$HOST:$BACKEND_PORT/app/
DESKTOP_ENABLED=$DESKTOP_ENABLED
DESKTOP_SKIP_REASON=$DESKTOP_SKIP_REASON
BACKEND_PID=$BACKEND_PID
FRONTEND_PID=$FRONTEND_PID
DESKTOP_PID=$DESKTOP_PID
EOF
}

run_quick_doctor
assert_node_version
assert_aimemo_not_already_running
warn_linux_file_watch_limit
warn_invalid_proxy_scheme
ensure_frontend_dependencies
ensure_frontend_dist_for_backend_app

HOST="${AIMEMO_HOST:-127.0.0.1}"
PREFERRED_BACKEND_PORT="${AIMEMO_BACKEND_PORT:-8000}"
PREFERRED_FRONTEND_PORT="${AIMEMO_FRONTEND_PORT:-5173}"
PREFERRED_DESKTOP_PORT="${AIMEMO_DESKTOP_PORT:-1420}"
BACKEND_PORT="$(find_available_port "$HOST" "$PREFERRED_BACKEND_PORT")"
FRONTEND_PORT="$(find_available_port "$HOST" "$PREFERRED_FRONTEND_PORT")"
DESKTOP_PORT="$(find_available_port "$HOST" "$PREFERRED_DESKTOP_PORT")"
DESKTOP_ENABLED=0
if ensure_desktop_dependencies; then
  DESKTOP_ENABLED=1
fi
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
if [[ "$DESKTOP_ENABLED" -eq 1 && "$(uname -s)" != "Linux" ]]; then
  print_port_fallback "Memo Elf webview" "$PREFERRED_DESKTOP_PORT" "$DESKTOP_PORT"
fi
echo "Backend:  http://$HOST:$BACKEND_PORT"
echo "Frontend: http://$HOST:$FRONTEND_PORT/app/"
echo "Product:  http://$HOST:$BACKEND_PORT/app/"
if [[ "$DESKTOP_ENABLED" -eq 1 ]]; then
  echo "Memo Elf: Tauri desktop window"
elif [[ -n "$DESKTOP_SKIP_REASON" ]]; then
  echo "Memo Elf: skipped ($DESKTOP_SKIP_REASON)"
fi

"$SCRIPT_DIR/start-backend.sh" $START_ARGS --host="$HOST" --port="$BACKEND_PORT" &
BACKEND_PID=$!
FRONTEND_PID=""
DESKTOP_PID=""

cleanup() {
  echo "Stopping AiMemo dev services..."
  local pids=()
  [[ -n "${BACKEND_PID:-}" ]] && pids+=("$BACKEND_PID")
  [[ -n "${FRONTEND_PID:-}" ]] && pids+=("$FRONTEND_PID")
  [[ -n "${DESKTOP_PID:-}" ]] && pids+=("$DESKTOP_PID")
  if [[ "${#pids[@]}" -gt 0 ]]; then
    kill "${pids[@]}" 2>/dev/null || true
  fi
  rm -f "$DEV_PID_FILE" "$DEV_STATUS_FILE"
}
trap cleanup INT TERM EXIT

sleep 2
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  wait "$BACKEND_PID" || true
  echo "AiMemo backend failed to start. Fix the backend error above, then rerun ./scripts/start-dev.sh." >&2
  exit 1
fi
wait_for_http "Backend" "http://$HOST:$BACKEND_PORT/api/health" 90
"$SCRIPT_DIR/start-frontend.sh" $START_ARGS --host="$HOST" --port="$FRONTEND_PORT" --backend-port="$BACKEND_PORT" &
FRONTEND_PID=$!
wait_for_http "Frontend" "http://$HOST:$FRONTEND_PORT/app/" 60

if [[ "$DESKTOP_ENABLED" -eq 1 ]]; then
  (
    cd "$REPO_ROOT/desktop"
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

write_start_status "ready"

if [[ "$DESKTOP_ENABLED" -eq 1 ]]; then
  wait "$BACKEND_PID" "$FRONTEND_PID" "$DESKTOP_PID"
else
  wait "$BACKEND_PID" "$FRONTEND_PID"
fi
