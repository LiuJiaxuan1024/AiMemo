#!/usr/bin/env bash

port_is_available() {
  local host="$1"
  local port="$2"

  python3 - "$host" "$port" <<'PY' >/dev/null 2>&1
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
