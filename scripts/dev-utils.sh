#!/usr/bin/env bash

port_is_available() {
  local host="$1"
  local port="$2"
  local python_bin

  if command -v node >/dev/null 2>&1; then
    node - "$host" "$port" <<'JS' >/dev/null 2>&1
const net = require("node:net");
const host = process.argv[2];
const port = Number(process.argv[3]);
const server = net.createServer();
server.once("error", () => process.exit(1));
server.once("listening", () => server.close(() => process.exit(0)));
server.listen(port, host);
JS
    return $?
  fi

  for python_bin in python3.12 python3 python; do
    if command -v "$python_bin" >/dev/null 2>&1; then
      break
    fi
    python_bin=""
  done

  if [[ -z "$python_bin" ]]; then
    echo "Node.js or Python is required for port probing. Install Node.js 20+ and Python 3.12, then rerun the dev script." >&2
    return 1
  fi

  "$python_bin" - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind((host, port))
PY
}

find_available_port() {
  local host="$1"
  local preferred_port="$2"
  local max_attempts="${3:-100}"
  local port="$preferred_port"
  local attempts=0

  while (( attempts < max_attempts )); do
    if port_is_available "$host" "$port"; then
      echo "$port"
      return 0
    fi
    port=$((port + 1))
    attempts=$((attempts + 1))
  done

  echo "Could not find a free port starting at $preferred_port for $host." >&2
  return 1
}

print_port_fallback() {
  local name="$1"
  local preferred_port="$2"
  local actual_port="$3"

  if [[ "$preferred_port" != "$actual_port" ]]; then
    echo "$name port $preferred_port is busy; using $actual_port instead."
  fi
}

read_project_config_value() {
  local path="$1"
  local default_value="$2"
  local config_path="$REPO_ROOT/config.json5"
  local script_dir

  if [[ ! -f "$config_path" ]]; then
    echo "$default_value"
    return 0
  fi

  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  node "$script_dir/read-project-config.cjs" "$config_path" "$path" "$default_value"
}
