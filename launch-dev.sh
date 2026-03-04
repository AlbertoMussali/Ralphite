#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
RUNNER_INTERVAL_SECONDS="${RUNNER_INTERVAL_SECONDS:-2}"
WORKSPACE_ROOT="${RALPHITE_WORKSPACE_ROOT:-$ROOT_DIR}"
SKIP_SYNC=false
SKIP_PNPM_INSTALL=false

usage() {
  cat <<'EOF'
Usage: ./launch-dev.sh [options]

Options:
  --workspace-root PATH   Workspace root for local runner (default: current repo root)
  --api-port PORT         API port (default: 8000)
  --web-port PORT         Web port (default: 5173)
  --runner-interval SEC   Runner poll interval seconds (default: 2)
  --skip-sync             Skip `uv sync`
  --skip-pnpm-install     Skip `pnpm install`
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-root)
      WORKSPACE_ROOT="$2"
      shift 2
      ;;
    --api-port)
      API_PORT="$2"
      shift 2
      ;;
    --web-port)
      WEB_PORT="$2"
      shift 2
      ;;
    --runner-interval)
      RUNNER_INTERVAL_SECONDS="$2"
      shift 2
      ;;
    --skip-sync)
      SKIP_SYNC=true
      shift
      ;;
    --skip-pnpm-install)
      SKIP_PNPM_INSTALL=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -d "$WORKSPACE_ROOT" ]]; then
  echo "workspace root does not exist: $WORKSPACE_ROOT" >&2
  exit 1
fi
WORKSPACE_ROOT="$(cd "$WORKSPACE_ROOT" && pwd)"

need_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing required command: $cmd" >&2
    exit 1
  fi
}

need_cmd uv
need_cmd curl

if ! command -v pnpm >/dev/null 2>&1; then
  if command -v corepack >/dev/null 2>&1; then
    echo "pnpm not found; enabling through corepack..."
    corepack enable
    corepack prepare pnpm@9.12.0 --activate
  else
    echo "missing required command: pnpm (or corepack)" >&2
    exit 1
  fi
fi

if [[ "$SKIP_SYNC" != true ]]; then
  echo "Running uv sync..."
  uv sync --all-packages
fi

if [[ "$SKIP_PNPM_INSTALL" != true ]]; then
  echo "Running pnpm install..."
  pnpm install
fi

LOG_DIR="$ROOT_DIR/runtime/dev-logs"
mkdir -p "$LOG_DIR"

API_LOG="$LOG_DIR/api.log"
RUNNER_LOG="$LOG_DIR/runner.log"
WEB_LOG="$LOG_DIR/web.log"

PIDS=()
CLEANED_UP=0

cleanup() {
  if [[ "$CLEANED_UP" -eq 1 ]]; then
    return
  fi
  CLEANED_UP=1
  local rc=$?
  if [[ ${#PIDS[@]} -gt 0 ]]; then
    echo "Shutting down services..."
    for pid in "${PIDS[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" >/dev/null 2>&1 || true
      fi
    done
    for pid in "${PIDS[@]}"; do
      wait "$pid" >/dev/null 2>&1 || true
    done
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

start_api() {
  echo "Starting API on :$API_PORT"
  PYTHONPATH="$ROOT_DIR/apps/api/src:$ROOT_DIR/packages/schemas/python/src" \
    uv run python -m uvicorn ralphite_api.main:app --reload --host 0.0.0.0 --port "$API_PORT" \
    >"$API_LOG" 2>&1 &
  PIDS+=("$!")
}

wait_for_api() {
  local api_url="http://127.0.0.1:${API_PORT}/health"
  local attempts=90
  local i

  echo "Waiting for API health at $api_url ..."
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS "$api_url" >/dev/null 2>&1; then
      echo "API healthy."
      return 0
    fi

    # Fail fast if API process crashed.
    if [[ ${#PIDS[@]} -gt 0 ]] && ! kill -0 "${PIDS[0]}" >/dev/null 2>&1; then
      echo "API process exited early. Check $API_LOG" >&2
      return 1
    fi
    sleep 1
  done

  echo "API health check timed out. Check $API_LOG" >&2
  return 1
}

start_runner() {
  local api_base="http://127.0.0.1:${API_PORT}"
  echo "Starting runner against $api_base (workspace: $WORKSPACE_ROOT)"
  RALPHITE_RUNNER_INTERVAL_SECONDS="$RUNNER_INTERVAL_SECONDS" \
    PYTHONPATH="$ROOT_DIR/apps/runner/src:$ROOT_DIR/packages/schemas/python/src" \
      uv run python -m ralphite_runner.main --api-base "$api_base" --workspace-root "$WORKSPACE_ROOT" \
      >"$RUNNER_LOG" 2>&1 &
  PIDS+=("$!")
}

start_web() {
  echo "Starting web on :$WEB_PORT"
  pnpm --filter @ralphite/web dev --host 0.0.0.0 --port "$WEB_PORT" \
    >"$WEB_LOG" 2>&1 &
  PIDS+=("$!")
}

start_api
wait_for_api
start_runner
start_web

echo ""
echo "All services started."
echo "Web:        http://127.0.0.1:${WEB_PORT}"
echo "API:        http://127.0.0.1:${API_PORT}"
echo "API docs:   http://127.0.0.1:${API_PORT}/docs"
echo "Logs:"
echo "  API:      $API_LOG"
echo "  Runner:   $RUNNER_LOG"
echo "  Web:      $WEB_LOG"
echo ""
echo "Tail logs: tail -f \"$API_LOG\" \"$RUNNER_LOG\" \"$WEB_LOG\""
echo "Press Ctrl+C to stop all services."

while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      wait "$pid" >/dev/null 2>&1 || true
      echo "A service exited unexpectedly (pid=$pid). Check logs under $LOG_DIR" >&2
      exit 1
    fi
  done
  sleep 1
done
