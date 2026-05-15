#!/bin/bash

set -Euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

API_HOST="${BIO_HARNESS_UI_HOST:-127.0.0.1}"
API_PORT="${BIO_HARNESS_UI_PORT:-8000}"
WEB_HOST="${BIO_HARNESS_WEB_HOST:-127.0.0.1}"
WEB_PORT="${BIO_HARNESS_WEB_PORT:-5173}"
API_BASE="http://${API_HOST}:${API_PORT}"
WEB_URL="http://${WEB_HOST}:${WEB_PORT}/?setup=1"
LOG_DIR="$PROJECT_ROOT/workspace/setup_reports"
LOG_FILE="$LOG_DIR/launcher_$(date +%Y%m%d_%H%M%S).log"

API_PID=""
WEB_PID=""

mkdir -p "$LOG_DIR"

log() {
  printf '%s\n' "$*"
  printf '%s\n' "$*" >> "$LOG_FILE"
}

stop_children() {
  if [[ -n "$WEB_PID" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
}

api_ready() {
  python3 - "$API_BASE" <<'PY'
import sys
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")
try:
    with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except (OSError, urllib.error.URLError):
    raise SystemExit(1)
PY
}

wait_for_api() {
  local deadline
  deadline=$((SECONDS + 90))
  while (( SECONDS < deadline )); do
    if api_ready; then
      return 0
    fi
    sleep 1
  done
  return 1
}

web_ready() {
  python3 - "$WEB_URL" <<'PY'
import sys
import urllib.error
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=2) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except (OSError, urllib.error.URLError):
    raise SystemExit(1)
PY
}

wait_for_web() {
  local deadline
  deadline=$((SECONDS + 45))
  while (( SECONDS < deadline )); do
    if web_ready; then
      return 0
    fi
    sleep 1
  done
  return 1
}

command_available() {
  command -v "$1" >/dev/null 2>&1
}

trap stop_children EXIT INT TERM

log "Bio-Harness launcher"
log "Project: $PROJECT_ROOT"
log "Log: $LOG_FILE"
log ""

if ! command_available python3; then
  log "Python 3.10+ is required before Bio-Harness can start."
  log "Install Python, then double-click this launcher again."
  open "https://www.python.org/downloads/" >/dev/null 2>&1 || true
  read -r -p "Press Return to close this window. "
  exit 1
fi

if ! command_available npm; then
  log "Node.js/npm is required for the Bio-Harness web interface."
  log "Install Node.js, then double-click this launcher again."
  open "https://nodejs.org/" >/dev/null 2>&1 || true
  read -r -p "Press Return to close this window. "
  exit 1
fi

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" || ! -d "$PROJECT_ROOT/.pixi" ]]; then
  log "Preparing the Python and scientific-tool environment..."
  if ! python3 scripts/bootstrap_bioharness.py >> "$LOG_FILE" 2>&1; then
    log "Environment setup failed. See: $LOG_FILE"
    read -r -p "Press Return to close this window. "
    exit 1
  fi
else
  log "Python and Pixi environments already exist; skipping bootstrap."
fi

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  log "Setup did not create .venv/bin/python. See the log above for details."
  read -r -p "Press Return to close this window. "
  exit 1
fi

if [[ ! -d "$PROJECT_ROOT/apps/web/node_modules" ]]; then
  log "Installing web-interface packages..."
  if ! npm --prefix apps/web ci >> "$LOG_FILE" 2>&1; then
    log "Web-interface package installation failed. See: $LOG_FILE"
    read -r -p "Press Return to close this window. "
    exit 1
  fi
fi

if api_ready >/dev/null 2>&1; then
  log "Bio-Harness API is already running at $API_BASE."
else
  log "Starting Bio-Harness API at $API_BASE..."
  BIO_HARNESS_UI_HOST="$API_HOST" \
    BIO_HARNESS_UI_PORT="$API_PORT" \
    BIO_HARNESS_UI_RELOAD=0 \
    "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/ui_v2_api.py" >> "$LOG_FILE" 2>&1 &
  API_PID="$!"
  if ! wait_for_api; then
    log "The Bio-Harness API did not become ready. See: $LOG_FILE"
    read -r -p "Press Return to close this window. "
    exit 1
  fi
fi

log "Starting Bio-Harness web interface at http://${WEB_HOST}:${WEB_PORT}..."
VITE_API_BASE="$API_BASE" \
  npm --prefix apps/web run dev -- --host "$WEB_HOST" --port "$WEB_PORT" --strictPort \
  >> "$LOG_FILE" 2>&1 &
WEB_PID="$!"

if ! wait_for_web; then
  log "The Bio-Harness web interface did not become ready. See: $LOG_FILE"
  read -r -p "Press Return to close this window. "
  exit 1
fi

open "$WEB_URL" >/dev/null 2>&1 || true

log ""
log "Bio-Harness is open in your browser:"
log "$WEB_URL"
log ""
log "Use the setup wizard to start Ollama, choose or download a model, and run the mini preflight."
log "Keep this window open while using Bio-Harness. Close it or press Control-C to stop the local servers."
wait "$WEB_PID"
