#!/bin/bash
# Ralphex common utilities

set -euo pipefail

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

source "$SCRIPT_DIR/ralphex-retry.sh"
source "$SCRIPT_DIR/ralphex-task-parser.sh"

DEFAULT_MODEL="${DEFAULT_MODEL:-gpt-5.3-codex}"
MODEL="${RALPHEX_MODEL:-$DEFAULT_MODEL}"
SANDBOX="${RALPHEX_SANDBOX:-workspace-write}"
MAX_ITERATIONS="${MAX_ITERATIONS:-20}"

RALPHEX_WARN_TOKENS="${RALPHEX_WARN_TOKENS:-380000}"
RALPHEX_ROTATE_TOKENS="${RALPHEX_ROTATE_TOKENS:-400000}"
export RALPHEX_WARN_TOKENS RALPHEX_ROTATE_TOKENS

STATE_DIR_NAME=".ralphex"
LEGACY_STATE_DIR_NAME=".ralph"
WORKTREES_DIR_NAME=".ralphex-worktrees"
LEGACY_WORKTREES_DIR_NAME=".ralph-worktrees"
TASK_FILE_NAME="RALPHEX_TASK.md"
LEGACY_TASK_FILE_NAME="RALPH_TASK.md"

ralphex_state_dir() {
  local workspace="${1:-.}"
  echo "$workspace/$STATE_DIR_NAME"
}

ralphex_worktrees_dir() {
  local workspace="${1:-.}"
  echo "$workspace/$WORKTREES_DIR_NAME"
}

ralphex_task_file() {
  local workspace="${1:-.}"
  if [[ -f "$workspace/$TASK_FILE_NAME" ]]; then
    echo "$workspace/$TASK_FILE_NAME"
  else
    echo "$workspace/$LEGACY_TASK_FILE_NAME"
  fi
}

migrate_legacy_ralph_state() {
  local workspace="${1:-.}"

  if [[ -d "$workspace/$LEGACY_STATE_DIR_NAME" && ! -d "$workspace/$STATE_DIR_NAME" ]]; then
    mv "$workspace/$LEGACY_STATE_DIR_NAME" "$workspace/$STATE_DIR_NAME" 2>/dev/null || true
  fi
  if [[ -d "$workspace/$LEGACY_WORKTREES_DIR_NAME" && ! -d "$workspace/$WORKTREES_DIR_NAME" ]]; then
    mv "$workspace/$LEGACY_WORKTREES_DIR_NAME" "$workspace/$WORKTREES_DIR_NAME" 2>/dev/null || true
  fi
}

get_iteration() {
  local workspace="${1:-.}"
  local f
  f="$(ralphex_state_dir "$workspace")/.iteration"
  [[ -f "$f" ]] && cat "$f" || echo "0"
}

set_iteration() {
  local workspace="$1"
  local iteration="$2"
  mkdir -p "$(ralphex_state_dir "$workspace")"
  echo "$iteration" > "$(ralphex_state_dir "$workspace")/.iteration"
}

increment_iteration() {
  local workspace="${1:-.}"
  local current
  current=$(get_iteration "$workspace")
  current=$((current + 1))
  set_iteration "$workspace" "$current"
  echo "$current"
}

log_activity() {
  local workspace="$1"
  local msg="$2"
  mkdir -p "$(ralphex_state_dir "$workspace")"
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$msg" >> "$(ralphex_state_dir "$workspace")/activity.log"
}

log_error() {
  local workspace="$1"
  local msg="$2"
  mkdir -p "$(ralphex_state_dir "$workspace")"
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$msg" >> "$(ralphex_state_dir "$workspace")/errors.log"
}

log_progress() {
  local workspace="$1"
  local msg="$2"
  local f
  f="$(ralphex_state_dir "$workspace")/progress.md"
  mkdir -p "$(ralphex_state_dir "$workspace")"
  [[ -f "$f" ]] || echo -e "# Progress Log\n" > "$f"
  echo "" >> "$f"
  echo "### $(date '+%Y-%m-%d %H:%M:%S')" >> "$f"
  echo "$msg" >> "$f"
}

init_ralphex_dir() {
  local workspace="$1"
  migrate_legacy_ralph_state "$workspace"
  local d
  d="$(ralphex_state_dir "$workspace")"
  mkdir -p "$d"

  [[ -f "$d/.iteration" ]] || echo "0" > "$d/.iteration"
  [[ -f "$d/session_id" ]] || : > "$d/session_id"

  if [[ ! -f "$d/guardrails.md" ]]; then
    cat > "$d/guardrails.md" <<'EOT'
# Ralphex Guardrails (Signs)

## Core Signs

### Sign: Read Before Writing
- **Trigger**: Before modifying any file
- **Instruction**: Always read the existing file first

### Sign: Test After Changes
- **Trigger**: After any code change
- **Instruction**: Run tests to verify nothing broke

### Sign: Commit Checkpoints
- **Trigger**: Before risky changes
- **Instruction**: Commit current working state first
EOT
  fi

  [[ -f "$d/progress.md" ]] || cat > "$d/progress.md" <<'EOT'
# Progress Log

## Summary

- Iterations completed: 0
- Current status: Initialized
EOT

  [[ -f "$d/activity.log" ]] || cat > "$d/activity.log" <<'EOT'
# Activity Log
EOT

  [[ -f "$d/errors.log" ]] || cat > "$d/errors.log" <<'EOT'
# Error Log
EOT
}

is_git_repo() {
  local workspace="${1:-.}"
  git -C "$workspace" rev-parse --git-dir >/dev/null 2>&1
}

extract_test_command() {
  local workspace="${1:-.}"
  local task_file
  task_file="$(ralphex_task_file "$workspace")"
  awk '
    BEGIN {in_yaml=0; done=0}
    /^---[[:space:]]*$/ {
      if (in_yaml==0) {in_yaml=1; next}
      else {done=1; exit}
    }
    in_yaml==1 && done==0 && /^test_command:[[:space:]]*/ {
      line=$0
      sub(/^test_command:[[:space:]]*/, "", line)
      gsub(/^"|"$/, "", line)
      gsub(/^\047|\047$/, "", line)
      print line
      exit
    }
  ' "$task_file"
}

check_task_complete() {
  local workspace="${1:-.}"
  local next
  next=$(get_next_task "$workspace" || true)
  [[ -z "$next" ]]
}

show_banner() {
  cat <<'EOT'
═══════════════════════════════════════════════════════════════════
Ralphex: Codex Autonomous Loop
═══════════════════════════════════════════════════════════════════
EOT
}

show_task_summary() {
  local workspace="$1"
  local task_file
  task_file="$(ralphex_task_file "$workspace")"
  echo "Task file: $task_file"
  local progress
  progress=$(get_progress "$workspace" || echo "0|0")
  local done total
  done=$(echo "$progress" | cut -d'|' -f1)
  total=$(echo "$progress" | cut -d'|' -f2)
  echo "Progress: $done/$total"
}

build_iteration_prompt() {
  local workspace="$1"
  local task_id="$2"
  local task_desc="$3"
  local line_no="$4"

  cat <<EOT
You are running inside the Ralphex loop.

Focus ONLY on this task item from RALPHEX_TASK.md:
- id: $task_id
- line: $line_no
- description: $task_desc

Required behavior:
1. Implement the needed code changes in this workspace.
2. Mark the target checkbox item as [x] when done.
3. Run relevant tests/commands to validate your changes.
4. Update .ralphex/progress.md with a concise summary.
5. If all task checkboxes are complete, output exactly: <ralphex>COMPLETE</ralphex>
6. If you are stuck after repeated failed attempts, output exactly: <ralphex>GUTTER</ralphex>

Read these files first:
- RALPHEX_TASK.md
- .ralphex/guardrails.md
- .ralphex/progress.md
- .ralphex/errors.log
EOT
}

_reset_session() {
  local workspace="$1"
  : > "$(ralphex_state_dir "$workspace")/session_id"
}

_run_codex_once() {
  local workspace="$1"
  local prompt="$2"
  local script_dir="$3"
  local session_file
  session_file="$(ralphex_state_dir "$workspace")/session_id"
  local session_id=""

  [[ -f "$session_file" ]] || : > "$session_file"
  session_id=$(tr -d '[:space:]' < "$session_file" 2>/dev/null || true)

  local -a cmd
  local skip_repo_flag=""
  if ! is_git_repo "$workspace"; then
    skip_repo_flag="--skip-git-repo-check"
  fi

  if [[ -n "$session_id" ]]; then
    cmd=(codex exec resume --json)
    [[ -n "$MODEL" ]] && cmd+=(--model "$MODEL")
    [[ -n "$skip_repo_flag" ]] && cmd+=("$skip_repo_flag")
    cmd+=("$session_id" "$prompt")
    log_activity "$workspace" "Ralphex turn (resume): session=$session_id"
  else
    cmd=(codex exec --json --sandbox "$SANDBOX")
    [[ -n "$MODEL" ]] && cmd+=(--model "$MODEL")
    [[ -n "$skip_repo_flag" ]] && cmd+=("$skip_repo_flag")
    cmd+=("$prompt")
    log_activity "$workspace" "Ralphex turn (new session)"
  fi

  local raw_file ctrl_file
  raw_file=$(mktemp)
  ctrl_file=$(mktemp)

  set +e
  (
    cd "$workspace" || exit 1
    "${cmd[@]}"
  ) 2>&1 | tee "$raw_file" | "$script_dir/ralphex-stream-parser.sh" "$workspace" > "$ctrl_file"
  local rc=${PIPESTATUS[0]}
  set -e

  cat "$ctrl_file"

  local raw
  raw=$(cat "$raw_file")
  rm -f "$raw_file" "$ctrl_file"

  if [[ "$rc" -ne 0 ]]; then
    echo "$raw" >&2
  fi

  return "$rc"
}

run_codex_turn() {
  local workspace="$1"
  local prompt="$2"
  local script_dir="$3"
  local attempts="${4:-3}"

  local attempt=1
  local controls=""

  while [[ "$attempt" -le "$attempts" ]]; do
    set +e
    controls=$(_run_codex_once "$workspace" "$prompt" "$script_dir")
    local rc=$?
    set -e

    if [[ "$rc" -eq 0 ]]; then
      echo "$controls"
      return 0
    fi

    if [[ "$attempt" -ge "$attempts" ]]; then
      log_error "$workspace" "Ralphex command failed after $attempt attempts"
      return "$rc"
    fi

    if is_retryable_error "$controls"; then
      local delay
      delay=$((2 * attempt))
      log_error "$workspace" "Retryable Codex error. attempt=$attempt delay=${delay}s"
      sleep "$delay"
      attempt=$((attempt + 1))
      continue
    fi

    log_error "$workspace" "Non-retryable Codex failure"
    return "$rc"
  done

  return 1
}

run_iteration() {
  local workspace="$1"
  local script_dir="$2"

  local next_task
  next_task=$(get_next_task "$workspace" || true)
  if [[ -z "$next_task" ]]; then
    echo "COMPLETE"
    return 0
  fi

  local task_id task_desc line_no
  task_id=$(echo "$next_task" | cut -d'|' -f1)
  task_desc=$(echo "$next_task" | cut -d'|' -f2)
  line_no=$(echo "$next_task" | cut -d'|' -f3)

  log_activity "$workspace" "Working task: $task_id ($task_desc)"

  local prompt
  prompt=$(build_iteration_prompt "$workspace" "$task_id" "$task_desc" "$line_no")

  local controls
  controls=$(run_codex_turn "$workspace" "$prompt" "$script_dir" 3)

  local test_cmd
  test_cmd=$(extract_test_command "$workspace" || true)
  if [[ -n "$test_cmd" ]]; then
    set +e
    (cd "$workspace" && eval "$test_cmd") >> "$(ralphex_state_dir "$workspace")/activity.log" 2>> "$(ralphex_state_dir "$workspace")/errors.log"
    local test_rc=$?
    set -e
    if [[ "$test_rc" -ne 0 ]]; then
      log_error "$workspace" "Test command failed: $test_cmd"
    else
      log_activity "$workspace" "Test command passed: $test_cmd"
    fi
  fi

  local iter
  iter=$(increment_iteration "$workspace")
  log_progress "$workspace" "Iteration $iter: completed turn for $task_id"

  if echo "$controls" | grep -q '^GUTTER$'; then
    echo "GUTTER"
    return 0
  fi

  if echo "$controls" | grep -q '^ROTATE$'; then
    _reset_session "$workspace"
    echo "ROTATE"
    return 0
  fi

  if check_task_complete "$workspace" || echo "$controls" | grep -q '^COMPLETE$'; then
    echo "COMPLETE"
    return 0
  fi

  echo "CONTINUE"
}

run_ralphex_loop() {
  local workspace="$1"
  local script_dir="$2"

  local iter=0
  while [[ "$iter" -lt "$MAX_ITERATIONS" ]]; do
    iter=$((iter + 1))
    echo "Iteration $iter/$MAX_ITERATIONS"

    local result
    result=$(run_iteration "$workspace" "$script_dir")

    case "$result" in
      COMPLETE)
        echo "Ralphex: task complete"
        return 0
        ;;
      GUTTER)
        echo "Ralphex: gutter detected"
        return 2
        ;;
      ROTATE)
        echo "Ralphex: rotating session"
        ;;
      CONTINUE)
        ;;
      *)
        echo "Ralphex: unknown iteration result: $result" >&2
        ;;
    esac
  done

  echo "Ralphex: reached MAX_ITERATIONS=$MAX_ITERATIONS"
  return 1
}

check_prerequisites() {
  local workspace="$1"
  local task_file
  task_file="$(ralphex_task_file "$workspace")"

  if [[ ! -f "$task_file" ]]; then
    echo "Missing task file: $task_file" >&2
    return 1
  fi

  if ! command -v codex >/dev/null 2>&1; then
    echo "codex CLI not found in PATH" >&2
    return 1
  fi

  if ! command -v jq >/dev/null 2>&1; then
    echo "jq not found in PATH" >&2
    return 1
  fi

  return 0
}
