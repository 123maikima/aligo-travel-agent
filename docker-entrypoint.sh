#!/usr/bin/env bash
set -euo pipefail

wait_for_tcp() {
  local host="$1"
  local port="$2"
  local name="$3"
  local timeout="${4:-60}"

  python - "$host" "$port" "$name" "$timeout" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
name = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.time() + timeout
last_error = None

while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"{name} is ready at {host}:{port}")
            raise SystemExit(0)
    except OSError as exc:
        last_error = exc
        time.sleep(1)

print(f"Timed out waiting for {name} at {host}:{port}: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

if [[ "${REDIS_ENABLED:-true}" == "true" ]]; then
  wait_for_tcp "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}" "Redis"
fi

if [[ "${POSTGRES_ENABLED:-false}" == "true" ]]; then
  wait_for_tcp "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "PostgreSQL"
  python scripts/init_postgres_schema.py
fi

exec "$@"
