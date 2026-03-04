#!/bin/bash
# Ralphex loop runner

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-common.sh"
source "$SCRIPT_DIR/ralphex-ui.sh"
source "$SCRIPT_DIR/ralphex-parallel.sh"
source "$SCRIPT_DIR/ralphex-orchestrator.sh"

PARALLEL_MODE=false
MAX_PARALLEL=3
USE_BRANCH=""
OPEN_PR=false
SKIP_CONFIRM=false
WORKSPACE=""
RESUME_RUN_ID=""
BASE_BRANCH=""

show_help() {
  cat <<'EOT'
Ralphex Loop

Usage:
  ./ralphex-loop.sh [options] [workspace]

Options:
  -m, --model MODEL
  -n, --iterations N
  -s, --sandbox MODE              (workspace-write|danger-full-access|read-only)
  --parallel
  --max-parallel N
  --branch NAME
  --resume-run RUN_ID
  --pr
  -y, --yes
  -h, --help
EOT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--model)
      MODEL="$2"; shift 2 ;;
    -n|--iterations)
      MAX_ITERATIONS="$2"; shift 2 ;;
    -s|--sandbox)
      SANDBOX="$2"; shift 2 ;;
    --parallel)
      PARALLEL_MODE=true; shift ;;
    --max-parallel)
      MAX_PARALLEL="$2"; PARALLEL_MODE=true; shift 2 ;;
    --branch)
      USE_BRANCH="$2"; shift 2 ;;
    --resume-run)
      RESUME_RUN_ID="$2"; PARALLEL_MODE=true; shift 2 ;;
    --pr)
      OPEN_PR=true; shift ;;
    -y|--yes)
      SKIP_CONFIRM=true; shift ;;
    -h|--help)
      show_help; exit 0 ;;
    -*)
      echo "Unknown option: $1" >&2; exit 1 ;;
    *)
      WORKSPACE="$1"; shift ;;
  esac
done

if [[ -z "$WORKSPACE" ]]; then
  WORKSPACE="$(pwd)"
else
  WORKSPACE="$(cd "$WORKSPACE" && pwd)"
fi

show_banner
ui_live_init
trap 'ui_live_stop' EXIT

run_header_id="${RESUME_RUN_ID:-$(date '+%Y%m%d%H%M%S')}"
export RALPHEX_RUN_ID_HINT="$run_header_id"

if ! check_prerequisites "$WORKSPACE"; then
  exit 1
fi

init_ralphex_dir "$WORKSPACE"
if ! ui_run_doctor_or_exit "$WORKSPACE"; then
  exit 2
fi

if git -C "$WORKSPACE" show-ref --verify --quiet refs/heads/main; then
  BASE_BRANCH="main"
elif git -C "$WORKSPACE" show-ref --verify --quiet refs/heads/master; then
  BASE_BRANCH="master"
else
  BASE_BRANCH="$(git -C "$WORKSPACE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
fi

run_mode="sequential"
if [[ "$PARALLEL_MODE" == true && -n "$RESUME_RUN_ID" ]]; then
  run_mode="parallel-resume"
elif [[ "$PARALLEL_MODE" == true ]]; then
  run_mode="parallel"
fi
ui_print_run_header "$WORKSPACE" "$run_header_id" "$BASE_BRANCH" "$run_mode" "$MODEL" "$SANDBOX"
ui_print_task_inventory "$WORKSPACE"
_ui_prefix "Plan" "Max iterations: $MAX_ITERATIONS | Max parallel: $MAX_PARALLEL"
ui_live_update "plan" "run_id=$run_header_id mode=$run_mode model=$MODEL"

acquire_lock() {
  local workspace="$1"
  local run_id="$2"
  local mode="$3"
  local lockdir="$workspace/.ralphex/lockdir"
  local meta="$lockdir/meta"
  local cleanup_cmd
  printf -v cleanup_cmd 'rm -rf %q >/dev/null 2>&1 || true' "$lockdir"

  if mkdir "$lockdir" 2>/dev/null; then
    {
      echo "pid=$$"
      echo "run_id=$run_id"
      echo "mode=$mode"
      echo "started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    } >"$meta"
    trap "$cleanup_cmd" EXIT INT TERM
    return 0
  fi

  if [[ -f "$meta" ]]; then
    local pid
    pid=$(awk -F= '$1=="pid"{print $2}' "$meta")
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Ralphex is already running (pid=$pid). Lock: $meta" >&2
      return 1
    fi
    rm -rf "$lockdir" >/dev/null 2>&1 || true
    mkdir "$lockdir" 2>/dev/null || { echo "Failed to acquire lock: $lockdir" >&2; return 1; }
    {
      echo "pid=$$"
      echo "run_id=$run_id"
      echo "mode=$mode"
      echo "started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    } >"$meta"
    trap "$cleanup_cmd" EXIT INT TERM
    return 0
  fi

  echo "Failed to acquire lock: $lockdir" >&2
  return 1
}

echo "Preflight: validating model access..."
set +e
preflight_out=$(
  (cd "$WORKSPACE" && codex exec --json --sandbox read-only --model "$MODEL" "Reply with OK only.") 2>&1
)
preflight_rc=$?
set -e
if [[ "$preflight_rc" -ne 0 ]]; then
  echo "Preflight failed. Codex could not run with model: $MODEL" >&2
  echo "$preflight_out" | tail -n 25 >&2
  exit 2
fi
_ui_prefix "Doctor" "Model preflight passed."

if [[ -n "$USE_BRANCH" ]]; then
  git -C "$WORKSPACE" checkout -B "$USE_BRANCH"
fi

if [[ "$SKIP_CONFIRM" != true ]]; then
  read -r -p "Start Ralphex loop? [y/N] " reply
  if [[ ! "$reply" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

  if [[ "$PARALLEL_MODE" == true ]]; then
  # Fail fast on dirty workspace so worktrees are created from a committed baseline.
  if ! git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Parallel mode requires a git repository." >&2
    exit 1
  fi

  if [[ -n "$RESUME_RUN_ID" ]]; then
    acquire_lock "$WORKSPACE" "$RESUME_RUN_ID" "parallel-resume" || exit 3
    resume_parallel_run "$WORKSPACE" "$RESUME_RUN_ID"
    rc=$?
    exit "$rc"
  fi

  if [[ -n "$(git -C "$WORKSPACE" status --porcelain)" ]]; then
    # Allow untracked files, but refuse any modifications to tracked content.
    true
  fi

  if ! git -C "$WORKSPACE" diff --quiet; then
    echo "Workspace has modified tracked files; refusing to start parallel run." >&2
    git -C "$WORKSPACE" diff --name-only >&2
    exit 4
  fi
  if ! git -C "$WORKSPACE" diff --cached --quiet; then
    echo "Workspace has staged changes; refusing to start parallel run." >&2
    git -C "$WORKSPACE" diff --cached --name-only >&2
    exit 4
  fi
  if [[ -n "$(git -C "$WORKSPACE" ls-files -u 2>/dev/null)" ]]; then
    echo "Workspace has unresolved merge conflicts; refusing to start parallel run." >&2
    exit 4
  fi

  run_id="$run_header_id"
  acquire_lock "$WORKSPACE" "$run_id" "parallel" || exit 3

  run_parallel_tasks "$WORKSPACE" "$MAX_PARALLEL" "${USE_BRANCH:-}" "$MAX_ITERATIONS" "$run_id" "$BASE_BRANCH"
  rc=$?
  if [[ "$rc" -eq 0 ]]; then
    cleanup_parallel_run "$WORKSPACE" "$run_id" "${USE_BRANCH:-}" || true
  else
    echo "Parallel run failed at group barrier orchestrator. Resume with: ./ralphex-loop.sh --parallel --resume-run $run_id -y" >&2
  fi
else
  run_id="$run_header_id"
  acquire_lock "$WORKSPACE" "$run_id" "sequential-grouped" || exit 3
  run_parallel_tasks "$WORKSPACE" "1" "" "$MAX_ITERATIONS" "$run_id" "$BASE_BRANCH"
  rc=$?
fi

if [[ "$OPEN_PR" == true ]] && command -v gh >/dev/null 2>&1; then
  current_branch=$(git -C "$WORKSPACE" rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "main" ]] && [[ "$current_branch" != "master" ]]; then
    (cd "$WORKSPACE" && gh pr create --fill) || true
  fi
fi

exit "$rc"
