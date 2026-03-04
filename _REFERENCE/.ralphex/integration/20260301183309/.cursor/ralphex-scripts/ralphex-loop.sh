#!/bin/bash
# Ralphex loop runner

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-common.sh"
source "$SCRIPT_DIR/ralphex-parallel.sh"

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

if ! check_prerequisites "$WORKSPACE"; then
  exit 1
fi

init_ralphex_dir "$WORKSPACE"
show_task_summary "$WORKSPACE"

echo "Model: $MODEL"
echo "Sandbox: $SANDBOX"
echo "Max iterations: $MAX_ITERATIONS"

if git -C "$WORKSPACE" show-ref --verify --quiet refs/heads/main; then
  BASE_BRANCH="main"
elif git -C "$WORKSPACE" show-ref --verify --quiet refs/heads/master; then
  BASE_BRANCH="master"
else
  BASE_BRANCH="$(git -C "$WORKSPACE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
fi

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
    if [[ "$rc" -eq 0 ]]; then
      # Best-effort finalize: fast-forward main to integration branch recorded in run meta.
      meta="$WORKSPACE/.ralphex/parallel/$RESUME_RUN_ID/run.meta"
      integration_branch=$(awk -F= '$1=="integration_branch"{print $2}' "$meta" 2>/dev/null || true)
      if [[ -n "$integration_branch" ]]; then
        git -C "$WORKSPACE" checkout "$BASE_BRANCH" >/dev/null 2>&1 || true
        git -C "$WORKSPACE" merge --ff-only "$integration_branch" || true
      fi
    fi
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

  run_id=$(date '+%Y%m%d%H%M%S')
  integration_branch="${USE_BRANCH:-ralphex/integration-$run_id}"
  acquire_lock "$WORKSPACE" "$run_id" "parallel" || exit 3

  run_parallel_tasks "$WORKSPACE" "$MAX_PARALLEL" "$integration_branch" "$MAX_ITERATIONS" "$run_id" "$BASE_BRANCH"
  rc=$?
  if [[ "$rc" -eq 0 ]]; then
    # Ensure everything ends up on main via ff-only.
    git -C "$WORKSPACE" checkout "$BASE_BRANCH" >/dev/null 2>&1 || true
    if ! git -C "$WORKSPACE" merge --ff-only "$integration_branch"; then
      echo "Failed to fast-forward main to $integration_branch." >&2
      echo "Inspect with: git log $BASE_BRANCH..$integration_branch --oneline" >&2
      jobs_file="$WORKSPACE/.ralphex/parallel/$run_id/jobs.jsonl"
      if [[ -f "$jobs_file" ]]; then
        jq -nc --arg run_id "$run_id" --arg status "FINALIZE_MAIN_FAILED" --arg branch "$BASE_BRANCH" --arg integration "$integration_branch" '{ts:now|todateiso8601, run_id:$run_id, status:$status, base_branch:$branch, integration_branch:$integration}' >>"$jobs_file" || true
      fi
      exit 5
    fi
    jobs_file="$WORKSPACE/.ralphex/parallel/$run_id/jobs.jsonl"
    if [[ -f "$jobs_file" ]]; then
      jq -nc --arg run_id "$run_id" --arg status "FINALIZE_MAIN_OK" --arg branch "$BASE_BRANCH" --arg integration "$integration_branch" '{ts:now|todateiso8601, run_id:$run_id, status:$status, base_branch:$branch, integration_branch:$integration}' >>"$jobs_file" || true
    fi
    cleanup_parallel_run "$WORKSPACE" "$run_id" "$integration_branch" || true
  else
    echo "Parallel run failed. Integration branch preserved: $integration_branch" >&2
    echo "Resume later with: ./ralphex-loop.sh --parallel --resume-run $run_id -y" >&2
    jobs_file="$WORKSPACE/.ralphex/parallel/$run_id/jobs.jsonl"
    if [[ -f "$jobs_file" ]]; then
      jq -nc --arg run_id "$run_id" --arg status "FINALIZE_MAIN_FAILED" --arg branch "$BASE_BRANCH" --arg integration "$integration_branch" '{ts:now|todateiso8601, run_id:$run_id, status:$status, base_branch:$branch, integration_branch:$integration}' >>"$jobs_file" || true
    fi
  fi
else
  acquire_lock "$WORKSPACE" "serial-$$" "serial" || exit 3
  run_ralphex_loop "$WORKSPACE" "$SCRIPT_DIR"
  rc=$?
fi

if [[ "$OPEN_PR" == true ]] && command -v gh >/dev/null 2>&1; then
  current_branch=$(git -C "$WORKSPACE" rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "main" ]] && [[ "$current_branch" != "master" ]]; then
    (cd "$WORKSPACE" && gh pr create --fill) || true
  fi
fi

exit "$rc"
