#!/bin/bash
# Ralphex parallel runner (worktree based)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-common.sh"
source "$SCRIPT_DIR/ralphex-ui.sh"
source "$SCRIPT_DIR/ralphex-orchestrator.sh"

# When sourced from ralphex-common.sh these may already be set; keep defensive defaults.
MODEL="${MODEL:-${RALPHEX_MODEL:-$DEFAULT_MODEL}}"
SANDBOX="${SANDBOX:-${RALPHEX_SANDBOX:-workspace-write}}"

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

_run_log_json() {
  local jobs_file="$1"
  shift
  jq -nc "$@" >>"$jobs_file"
}

_progress_event() {
  local status_dir="$1"
  local run_id="$2"
  local group="$3"
  local task_id="$4"
  local stage="$5"
  local event="$6"
  local level="$7"
  local message="$8"
  local meta_json="${9:-{}}"
  ui_emit_standard_event "$status_dir" "$run_id" "$group" "$task_id" "$stage" "$event" "$level" "$message" "$meta_json" || true
}

_slot_map_add() {
  local map_file="$1"
  local task_id="$2"
  local slot_id="$3"
  local start_ts="$4"
  printf '%s|%s|%s\n' "$task_id" "$slot_id" "$start_ts" >>"$map_file"
}

_slot_map_get() {
  local map_file="$1"
  local task_id="$2"
  awk -F'|' -v t="$task_id" '$1==t{print $2 "|" $3; exit}' "$map_file" 2>/dev/null || true
}

_slot_map_remove() {
  local map_file="$1"
  local task_id="$2"
  local tmp="${map_file}.tmp"
  awk -F'|' -v t="$task_id" '$1!=t{print}' "$map_file" >"$tmp" 2>/dev/null || true
  mv "$tmp" "$map_file" 2>/dev/null || true
}

_summary_fallback_md() {
  local title="$1"
  local group="$2"
  local elapsed="$3"
  local diff_names="$4"
  local log_lines="$5"
  local task_changes="$6"
  cat <<EOT
## $title

### What changed
$diff_names

### Task coverage
$task_changes

### Risks/notes
- Auto-generated fallback summary (LLM unavailable or timed out).

### Validation state
- Not evaluated in summary stage.

### Next action
- Review commits and run validation if needed.

_Elapsed: ${elapsed}s${group:+ | group $group}_
EOT
}

_extract_agent_text_from_json_stream() {
  local json_file="$1"
  jq -rs '
    map(select(.type=="item.completed" and .item.type=="agent_message") | .item.text // "")
    | map(select(length>0))
    | join("\n\n")
  ' "$json_file" 2>/dev/null || true
}

_generate_summary_with_agent() {
  local workspace="$1"
  local prompt="$2"
  local timeout_sec="${3:-45}"
  local tmp_json
  tmp_json="$(mktemp)"
  local out=""
  set +e
  (
    cd "$workspace" || exit 1
    codex exec --json --sandbox read-only --model "$MODEL" "$prompt"
  ) >"$tmp_json" 2>&1
  local rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    out="$(_extract_agent_text_from_json_stream "$tmp_json")"
  fi
  rm -f "$tmp_json" >/dev/null 2>&1 || true
  printf '%s' "$out"
}

summarize_group_changes_with_agent() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local status_dir="$4"
  local base_sha="$5"
  local head_sha="$6"
  local elapsed_secs="${7:-0}"
  local jobs_file="$status_dir/jobs.jsonl"
  local summary_dir="$status_dir/summaries"
  local summary_path="$summary_dir/group-${group}.md"
  mkdir -p "$summary_dir"

  local diff_names log_lines task_changes outcomes
  diff_names=$(git -C "$workspace" diff --name-status "$base_sha..$head_sha" 2>/dev/null | sed -n '1,80p')
  log_lines=$(git -C "$workspace" log --oneline "$base_sha..$head_sha" 2>/dev/null | sed -n '1,80p')
  task_changes=$(git -C "$workspace" diff --unified=0 "$base_sha..$head_sha" -- RALPHEX_TASK.md RALPH_TASK.md 2>/dev/null | sed -n '1,120p')
  outcomes=$(jq -r --arg g "$group" 'select(.group==$g) | [.status, (.task_id // "-"), (.reason // "-")] | @tsv' "$jobs_file" 2>/dev/null | sed -n '1,120p')

  local prompt
  prompt=$(cat <<EOT
Create a concise markdown execution summary.
Output sections exactly:
## What changed
## Task coverage
## Risks/notes
## Validation state
## Next action

Context:
- Run ID: $run_id
- Group: $group
- Base SHA: $base_sha
- Head SHA: $head_sha
- Elapsed seconds: $elapsed_secs

Git diff (name-status):
$diff_names

Git log:
$log_lines

Task file delta:
$task_changes

Group job outcomes:
$outcomes
EOT
)

  local summary_text
  summary_text=$(_generate_summary_with_agent "$workspace" "$prompt")
  if [[ -z "${summary_text//[[:space:]]/}" ]]; then
    summary_text=$(_summary_fallback_md "Group $group Summary" "$group" "$elapsed_secs" "${diff_names:-"- none -"}" "${log_lines:-"- none -"}" "${task_changes:-"- none -"}")
  fi
  printf '%s\n' "$summary_text" >"$summary_path"
  _progress_event "$status_dir" "$run_id" "$group" "" "summary" "GROUP_SUMMARY_READY" "info" "group summary ready" "$(jq -nc --arg summary_path "$summary_path" '{summary_path:$summary_path}')"
  _ui_prefix "Group $group" "Summary ready: $summary_path"
}

summarize_run_changes_with_agent() {
  local workspace="$1"
  local run_id="$2"
  local status_dir="$3"
  local run_base_sha="$4"
  local run_head_sha="$5"
  local elapsed_secs="${6:-0}"
  local jobs_file="$status_dir/jobs.jsonl"
  local summary_dir="$status_dir/summaries"
  local summary_path="$summary_dir/final.md"
  mkdir -p "$summary_dir"

  local diff_names log_lines task_changes outcomes
  diff_names=$(git -C "$workspace" diff --name-status "$run_base_sha..$run_head_sha" 2>/dev/null | sed -n '1,200p')
  log_lines=$(git -C "$workspace" log --oneline "$run_base_sha..$run_head_sha" 2>/dev/null | sed -n '1,200p')
  task_changes=$(git -C "$workspace" diff --unified=0 "$run_base_sha..$run_head_sha" -- RALPHEX_TASK.md RALPH_TASK.md 2>/dev/null | sed -n '1,200p')
  outcomes=$(jq -r '[.status, (.group // "-"), (.task_id // "-"), (.reason // "-")] | @tsv' "$jobs_file" 2>/dev/null | sed -n '1,200p')

  local prompt
  prompt=$(cat <<EOT
Create a concise markdown final run summary.
Output sections exactly:
## What changed
## Task coverage
## Risks/notes
## Validation state
## Next action

Context:
- Run ID: $run_id
- Base SHA: $run_base_sha
- Head SHA: $run_head_sha
- Elapsed seconds: $elapsed_secs

Git diff (name-status):
$diff_names

Git log:
$log_lines

Task file delta:
$task_changes

Run job outcomes:
$outcomes
EOT
)

  local summary_text
  summary_text=$(_generate_summary_with_agent "$workspace" "$prompt")
  if [[ -z "${summary_text//[[:space:]]/}" ]]; then
    summary_text=$(_summary_fallback_md "Final Run Summary" "" "$elapsed_secs" "${diff_names:-"- none -"}" "${log_lines:-"- none -"}" "${task_changes:-"- none -"}")
  fi
  printf '%s\n' "$summary_text" >"$summary_path"
  _progress_event "$status_dir" "$run_id" "" "" "summary" "RUN_SUMMARY_READY" "info" "run summary ready" "$(jq -nc --arg summary_path "$summary_path" '{summary_path:$summary_path}')"
  _ui_prefix "Summary" "Final summary ready: $summary_path"
}

_sync_group_slot_updates() {
  local progress_file="$1"
  local cursor="${2:-0}"
  local map_file="$3"
  local group="$4"
  local total
  total=$(wc -l <"$progress_file" 2>/dev/null | tr -d ' ')
  [[ "$total" =~ ^[0-9]+$ ]] || total=0
  [[ "$cursor" =~ ^[0-9]+$ ]] || cursor=0
  if [[ "$total" -le "$cursor" ]]; then
    echo "$cursor"
    return 0
  fi

  local now
  now=$(date +%s 2>/dev/null || echo 0)
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" ]] || continue
    local ev_group task_id event stage message level
    ev_group=$(jq -r '.group // ""' <<<"$line" 2>/dev/null || echo "")
    [[ "$ev_group" == "$group" ]] || continue
    task_id=$(jq -r '.task_id // ""' <<<"$line" 2>/dev/null || echo "")
    [[ -n "$task_id" ]] || continue
    event=$(jq -r '.event // ""' <<<"$line" 2>/dev/null || echo "")
    stage=$(jq -r '.stage // ""' <<<"$line" 2>/dev/null || echo "")
    message=$(jq -r '.message // ""' <<<"$line" 2>/dev/null || echo "")
    level=$(jq -r '.level // "info"' <<<"$line" 2>/dev/null || echo "info")
    local slot_info
    slot_info=$(_slot_map_get "$map_file" "$task_id")
    [[ -n "$slot_info" ]] || continue
    local slot_id start_ts elapsed
    slot_id=$(echo "$slot_info" | cut -d'|' -f1)
    start_ts=$(echo "$slot_info" | cut -d'|' -f2)
    [[ "$start_ts" =~ ^[0-9]+$ ]] || start_ts="$now"
    elapsed=$((now - start_ts))
    (( elapsed < 0 )) && elapsed=0
    case "$event" in
      TASK_HEARTBEAT)
        ui_slot_update "$slot_id" "running" "$message" "$level" "$elapsed"
        ;;
      MERGE_FIX_STARTED|MERGE_FIX_PROGRESS)
        ui_slot_update "$slot_id" "merge-fix" "$message" "$level" "$elapsed"
        ;;
      MERGE_FIX_FAILED)
        ui_slot_update "$slot_id" "merge-fix" "$message" "error" "$elapsed"
        ;;
      TASK_RESULT)
        if [[ "$stage" == "task" ]]; then
          ui_slot_update "$slot_id" "finishing" "$message" "$level" "$elapsed"
        fi
        ;;
    esac
  done < <(sed -n "$((cursor + 1)),$total p" "$progress_file" 2>/dev/null)

  echo "$total"
}

_read_agents_md_snippet() {
  local dir="$1"
  local f="$dir/AGENTS.md"
  if [[ -f "$f" ]]; then
    sed -n '1,240p' "$f"
  fi
}

_sync_agent_context() {
  local workspace="$1"
  local wt_dir="$2"
  mkdir -p "$wt_dir/.ralphex"
  cp -f "$(ralphex_state_dir "$workspace")/guardrails.md" "$wt_dir/.ralphex/guardrails.md" 2>/dev/null || true
  cp -f "$(ralphex_state_dir "$workspace")/progress.md" "$wt_dir/.ralphex/progress.md" 2>/dev/null || true
  cp -f "$(ralphex_state_dir "$workspace")/errors.log" "$wt_dir/.ralphex/errors.log" 2>/dev/null || true
  cp -f "$(ralphex_state_dir "$workspace")/activity.log" "$wt_dir/.ralphex/activity.log" 2>/dev/null || true
  cp -f "$workspace/AGENTS.md" "$wt_dir/AGENTS.md" 2>/dev/null || true
}

_create_integration_worktree() {
  local workspace="$1"
  local run_id="$2"
  local integration_branch="$3"
  local base_ref="$4"
  local status_dir
  status_dir="$(ralphex_state_dir "$workspace")/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local integration_dir
  if [[ "$integration_branch" =~ -g([0-9]+)$ ]]; then
    integration_dir="$(ralphex_state_dir "$workspace")/integration/$run_id/g${BASH_REMATCH[1]}"
  else
    integration_dir="$(ralphex_state_dir "$workspace")/integration/$run_id"
  fi

  mkdir -p "$status_dir" "$(dirname "$integration_dir")"

  if [[ -d "$integration_dir" ]]; then
    echo "$integration_dir"
    return 0
  fi

  if git -C "$workspace" show-ref --verify --quiet "refs/heads/$integration_branch"; then
    git -C "$workspace" worktree add -f "$integration_dir" "$integration_branch" >>"$merge_log" 2>&1
  else
    git -C "$workspace" worktree add -f -b "$integration_branch" "$integration_dir" "$base_ref" >>"$merge_log" 2>&1
  fi
  echo "$integration_dir"
}

_run_agent_in_worktree() {
  local workspace="$1"
  local run_id="$2"
  local base_ref="$3"
  local task_id="$4"
  local task_desc="$5"
  local line_no="$6"
  local job_id="$7"
  local tools_required="${8:-}"
  local test_override="${9:-}"
  local slot_id="${10:-0}"

  local wt_base
  wt_base="$(ralphex_worktrees_dir "$workspace")/$run_id"
  local wt_dir="$wt_base/job-$job_id"
  local status_dir
  status_dir="$(ralphex_state_dir "$workspace")/parallel/$run_id"
  local status_file="$status_dir/job-$job_id.status"
  local log_file="$status_dir/job-$job_id.log"
  local jobs_file="$status_dir/jobs.jsonl"

  mkdir -p "$wt_base" "$status_dir"

  local branch="ralphex/parallel-${run_id}-${job_id}-${task_id}-$(slugify "$task_desc")"
  local task_group=""
  local task_row
  task_row=$(get_task_by_id "$workspace" "$task_id" || true)
  task_group=$(echo "$task_row" | cut -d'|' -f3)

  if ! git -C "$workspace" worktree add -f -b "$branch" "$wt_dir" "$base_ref" > "$log_file" 2>&1; then
    echo "FAILED|$task_id|$branch|$wt_dir|worktree_create||||$slot_id" > "$status_file"
    _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "FAILED" --arg reason "worktree_create" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "task" "TASK_RESULT" "error" "task failed: worktree create"
    return 0
  fi

  _sync_agent_context "$workspace" "$wt_dir"

  local agents_md
  agents_md=$(_read_agents_md_snippet "$wt_dir" || true)

  local default_test
  default_test=$(extract_test_command "$wt_dir" || true)
  local requested_test="${test_override:-$default_test}"

  local prompt
  prompt=$(cat <<EOT
You are running in Ralphex parallel mode.

Read and follow AGENTS.md FIRST. Content (truncated):
----------------
${agents_md:-"(AGENTS.md not found)"}
----------------

Complete ONLY this task from RALPHEX_TASK.md and then stop:
- id: $task_id
- line: $line_no
- description: $task_desc

Rules:
1. Implement required file changes for this task only.
2. Do NOT modify RALPHEX_TASK.md. The orchestrator will mark tasks complete.
3. Do NOT modify anything under .ralphex/.
4. Do NOT commit; leave changes unstaged or staged.
5. End with a concise summary.

Required tools for this task (if listed): ${tools_required:-"(none listed)"}
Test command to run for this task (best effort): ${requested_test:-"(none configured)"}

Read these files before acting:
- AGENTS.md
- RALPHEX_TASK.md
- .ralphex/guardrails.md
- .ralphex/progress.md
- .ralphex/errors.log
EOT
)

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "JOB_STARTED" --arg tools "$tools_required" --arg test "$requested_test" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, tools:$tools, test:$test}'
  _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "task" "TASK_STARTED" "info" "task started" "$(jq -nc --arg branch "$branch" --arg tools "$tools_required" --arg test "$requested_test" --arg task_label "$task_id" --arg slot_id "$slot_id" '{branch:$branch,tools:$tools,test:$test,task_label:$task_label,slot_id:($slot_id|tonumber)}')"

  set +e
  (
    cd "$wt_dir" || exit 1
    codex exec --json --sandbox "$SANDBOX" --model "$MODEL" "$prompt"
  ) 2>&1 \
    | tee -a "$log_file" \
    | RALPHEX_STREAM_STATUS_DIR="$status_dir" \
      RALPHEX_STREAM_RUN_ID="$run_id" \
      RALPHEX_STREAM_GROUP="$task_group" \
      RALPHEX_STREAM_TASK_ID="$task_id" \
      RALPHEX_STREAM_STAGE="task" \
      RALPHEX_REASONING_SUMMARY="${RALPHEX_REASONING_SUMMARY:-1}" \
      "$SCRIPT_DIR/ralphex-stream-parser.sh" "$wt_dir" >/dev/null
  local rc=${PIPESTATUS[0]}
  set -e

  if [[ "$rc" -eq 0 ]]; then
    # Enforce: never commit task/log changes from the agent worktree.
    git -C "$wt_dir" restore -SW -- RALPHEX_TASK.md RALPH_TASK.md 2>/dev/null || true
    rm -rf "$wt_dir/.ralphex" "$wt_dir/.ralph" >/dev/null 2>&1 || true

    if ! git -C "$wt_dir" diff --quiet; then
      git -C "$wt_dir" add -A
      git -C "$wt_dir" reset -q -- RALPHEX_TASK.md RALPH_TASK.md 2>/dev/null || true
      git -C "$wt_dir" reset -q -- .ralphex .ralph 2>/dev/null || true
      if ! git -C "$wt_dir" diff --cached --quiet; then
        if ! git -C "$wt_dir" -c user.name="ralphex" -c user.email="ralphex@local" commit -m "ralphex: complete $task_id" >> "$log_file" 2>&1; then
          echo "FAILED|$task_id|$branch|$wt_dir|commit_failed||||$slot_id" > "$status_file"
          _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "JOB_FAILED" --arg reason "commit_failed" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
          _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "task" "TASK_RESULT" "error" "task failed: commit failed" "$(jq -nc --arg reason "commit_failed" '{reason:$reason}')"
          return 0
        fi
      fi
    fi

    local sha
    sha=$(git -C "$wt_dir" rev-parse HEAD 2>/dev/null || echo "")
    echo "SUCCESS|$task_id|$branch|$wt_dir|ok|$sha|$tools_required|$requested_test|$slot_id" > "$status_file"
    _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "JOB_SUCCESS" --arg sha "$sha" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, sha:$sha}'
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "task" "TASK_RESULT" "info" "task completed" "$(jq -nc --arg branch "$branch" --arg sha "$sha" '{branch:$branch,sha:$sha}')"
  else
    echo "FAILED|$task_id|$branch|$wt_dir|codex_failed||||$slot_id" > "$status_file"
    _run_log_json "$jobs_file" --arg run_id "$run_id" --arg job_id "$job_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "JOB_FAILED" --arg reason "codex_failed" '{ts:now|todateiso8601, run_id:$run_id, job_id:($job_id|tonumber), task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "task" "TASK_RESULT" "error" "task failed: codex failed" "$(jq -nc --arg reason "codex_failed" '{reason:$reason}')"
  fi

  return 0
}

_merge_success_branch() {
  local repo_dir="$1"
  local branch="$2"
  local log_file="${3:-/dev/null}"

  # Prefer fast-forward merges for parallel task branches to avoid requiring
  # user git identity and to reduce merge commits/noise.
  if git -C "$repo_dir" merge --ff-only "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  # If ff-only is not possible, fall back to a merge commit with a local identity.
  if git -C "$repo_dir" -c user.name="ralphex" -c user.email="ralphex@local" merge --no-ff --no-edit "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  git -C "$repo_dir" merge --abort >/dev/null 2>&1 || true
  return 1
}

_cleanup_worktree() {
  local workspace="$1"
  local wt_dir="$2"
  git -C "$workspace" worktree remove -f "$wt_dir" >/dev/null 2>&1 || true
}

_auto_resolve_merge_conflict() {
  local workspace="$1"
  local run_id="$2"
  local integration_branch="$3"
  local task_id="$4"
  local task_branch="$5"
  local status_dir
  status_dir="$(ralphex_state_dir "$workspace")/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"
  local task_group=""
  local task_row
  task_row=$(get_task_by_id "$workspace" "$task_id" || true)
  task_group=$(echo "$task_row" | cut -d'|' -f3)

  local mergefix_branch="ralphex/mergefix-${run_id}-${task_id}"
  local fix_dir
  fix_dir="$(ralphex_state_dir "$workspace")/merge-fix/$run_id/$task_id"
  mkdir -p "$(dirname "$fix_dir")"

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$task_branch" --arg status "MERGE_FIX_STARTED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
  _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_STARTED" "warn" "merge-fix started" "$(jq -nc --arg branch "$task_branch" '{branch:$branch}')"

  local has_fix_worktree="false"
  if git -C "$workspace" worktree list --porcelain | awk '/^worktree /{print substr($0,10)}' | grep -Fxq "$fix_dir"; then
    has_fix_worktree="true"
  fi

  if [[ "$has_fix_worktree" == "false" ]]; then
    if git -C "$workspace" show-ref --verify --quiet "refs/heads/$mergefix_branch"; then
      if ! git -C "$workspace" worktree add -f "$fix_dir" "$mergefix_branch" >>"$merge_log" 2>&1; then
        echo "merge-fix worktree attach failed for $mergefix_branch" >>"$merge_log"
        _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_FAILED" "error" "merge-fix failed: worktree attach failed"
        return 1
      fi
    else
      if ! git -C "$workspace" worktree add -f -b "$mergefix_branch" "$fix_dir" "$integration_branch" >>"$merge_log" 2>&1; then
        echo "merge-fix worktree create failed for $mergefix_branch" >>"$merge_log"
        _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_FAILED" "error" "merge-fix failed: worktree create failed"
        return 1
      fi
    fi
  fi

  # Reuse-safe baseline: ensure prior failed attempts do not poison retries.
  if ! (
    cd "$fix_dir" &&
    git merge --abort >/dev/null 2>&1 || true
    git rebase --abort >/dev/null 2>&1 || true
    git cherry-pick --abort >/dev/null 2>&1 || true
    git reset --hard "$integration_branch" >/dev/null 2>&1
    git clean -fd >/dev/null 2>&1
  ) >>"$merge_log" 2>&1; then
    echo "merge-fix baseline reset failed for $mergefix_branch" >>"$merge_log"
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_FAILED" "error" "merge-fix failed: baseline reset failed"
    return 1
  fi
  _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_PROGRESS" "info" "merge-fix baseline prepared"

  set +e
  (cd "$fix_dir" && git merge "$task_branch") >>"$merge_log" 2>&1
  local merge_rc=$?
  set -e
  _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_PROGRESS" "info" "merge-fix merge attempted"

  if [[ "$merge_rc" -ne 0 ]]; then
    local conflicts
    conflicts=$(cd "$fix_dir" && git diff --name-only --diff-filter=U || true)

    local agents_md
    agents_md=$(_read_agents_md_snippet "$fix_dir" || true)

    local prompt
    prompt=$(cat <<EOT
You are resolving a git merge conflict in VoiceVault.

Read and follow AGENTS.md FIRST. Content (truncated):
----------------
${agents_md:-"(AGENTS.md not found)"}
----------------

Goal:
- Resolve merge conflicts and keep the intent of BOTH branches.

Hard rules:
1. Do NOT modify RALPHEX_TASK.md or anything under .ralphex/.
2. Prefer keeping both changes. If truly incompatible, keep the integration branch behavior unless the task branch is clearly the intended new behavior.
3. After resolving conflicts, run the repo test command if available and report results.

Conflict files (from git):
${conflicts:-"(unable to list conflicts)"}
EOT
)

    set +e
    (
      cd "$fix_dir" || exit 1
      codex exec --json --sandbox "$SANDBOX" --model "$MODEL" "$prompt"
    ) 2>&1 \
      | tee -a "$merge_log" \
      | RALPHEX_STREAM_STATUS_DIR="$status_dir" \
        RALPHEX_STREAM_RUN_ID="$run_id" \
        RALPHEX_STREAM_GROUP="$task_group" \
        RALPHEX_STREAM_TASK_ID="$task_id" \
        RALPHEX_STREAM_STAGE="merge_fix" \
        RALPHEX_REASONING_SUMMARY="${RALPHEX_REASONING_SUMMARY:-1}" \
        "$SCRIPT_DIR/ralphex-stream-parser.sh" "$fix_dir" >/dev/null
    local rc=${PIPESTATUS[0]}
    set -e
    if [[ "$rc" -ne 0 ]]; then
      echo "merge-fix codex failed for $mergefix_branch" >>"$merge_log"
      _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_FAILED" "error" "merge-fix failed: codex conflict resolution failed"
      return 1
    fi
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_PROGRESS" "info" "merge-fix codex conflict resolution completed"
  fi

  # Enforce: never commit task/log changes.
  (cd "$fix_dir" && git restore -SW -- RALPHEX_TASK.md RALPH_TASK.md 2>/dev/null || true)
  rm -rf "$fix_dir/.ralphex" "$fix_dir/.ralph" >/dev/null 2>&1 || true

  # Stage first so files resolved by codex are recorded from unmerged -> merged.
  (cd "$fix_dir" && git add -A)
  (cd "$fix_dir" && git reset -q -- RALPHEX_TASK.md RALPH_TASK.md 2>/dev/null || true)
  (cd "$fix_dir" && git reset -q -- .ralphex .ralph 2>/dev/null || true)

  local unresolved
  unresolved=$(cd "$fix_dir" && git diff --name-only --diff-filter=U || true)
  if [[ -n "$unresolved" ]]; then
    echo "merge-fix unresolved conflicts for $mergefix_branch: $unresolved" >>"$merge_log"
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_FAILED" "error" "merge-fix unresolved conflicts remain" "$(jq -nc --arg unresolved "$unresolved" '{unresolved:$unresolved}')"
    return 1
  fi
  _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_PROGRESS" "info" "merge-fix conflicts staged and resolved"

  if ! (cd "$fix_dir" && git -c user.name="ralphex" -c user.email="ralphex@local" commit --no-edit) >>"$merge_log" 2>&1; then
    echo "merge-fix commit failed for $mergefix_branch" >>"$merge_log"
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_FAILED" "error" "merge-fix failed: commit failed"
    return 1
  fi

  # Best-effort tests (do not block run; log failures).
  local test_cmd
  test_cmd=$(extract_test_command "$fix_dir" || true)
  if [[ -n "$test_cmd" ]]; then
    set +e
    (cd "$fix_dir" && eval "$test_cmd") >>"$merge_log" 2>&1
    local test_rc=$?
    set -e
    if [[ "$test_rc" -ne 0 ]]; then
      echo "merge-fix test command failed: $test_cmd" >>"$merge_log"
    fi
  fi

  # Merge the merge-fix branch into integration.
  local integration_dir
  integration_dir="$(_create_integration_worktree "$workspace" "$run_id" "$integration_branch" "$integration_branch")"
  if ! _merge_success_branch "$integration_dir" "$mergefix_branch" "$merge_log"; then
    echo "merge-fix failed to merge back into integration: $mergefix_branch" >>"$merge_log"
    _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_FAILED" "error" "merge-fix failed: merge back into integration failed"
    return 1
  fi

  git -C "$workspace" worktree remove -f "$fix_dir" >/dev/null 2>&1 || true
  git -C "$workspace" branch -D "$mergefix_branch" >/dev/null 2>&1 || true

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$task_branch" --arg status "MERGE_FIX_DONE" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
  _progress_event "$status_dir" "$run_id" "$task_group" "$task_id" "merge_fix" "MERGE_FIX_DONE" "info" "merge-fix merged back into integration"
  return 0
}

_mark_task_complete_in_integration() {
  local integration_dir="$1"
  local task_id="$2"
  local merge_log="$3"

  mark_task_complete "$integration_dir" "$task_id" || true
  local task_file_rel="RALPHEX_TASK.md"
  if [[ ! -f "$integration_dir/$task_file_rel" ]]; then
    task_file_rel="RALPH_TASK.md"
  fi
  if [[ -f "$integration_dir/$task_file_rel" ]] && ! git -C "$integration_dir" diff --quiet -- "$task_file_rel" 2>/dev/null; then
    git -C "$integration_dir" add "$task_file_rel" >/dev/null 2>&1 || true
    git -C "$integration_dir" -c user.name="ralphex" -c user.email="ralphex@local" commit -m "ralphex: mark $task_id complete" >>"$merge_log" 2>&1 || true
  fi
}

resume_parallel_run() {
  local workspace="$1"
  local run_id="$2"

  local status_dir
  status_dir="$(ralphex_state_dir "$workspace")/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"
  local meta="$status_dir/run.meta"

  [[ -f "$meta" ]] || { echo "No run meta found for run_id=$run_id" >&2; return 1; }

  local base_ref
  base_ref=$(awk -F= '$1=="base_ref"{print $2}' "$meta")
  [[ -n "$base_ref" ]] || base_ref="main"

  local groups
  groups=$(get_pending_groups "$workspace" || true)

  _ui_prefix "Plan" "Resuming run $run_id with group-barrier orchestrator"
  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "RESUME_STARTED" '{ts:now|todateiso8601, run_id:$run_id, status:$status}'
  _progress_event "$status_dir" "$run_id" "" "" "plan" "RUN_RESUMED" "info" "resume started"
  local resume_start_sha resume_start_ts
  resume_start_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "")
  resume_start_ts=$(date +%s 2>/dev/null || echo 0)

  local group
  while IFS= read -r group || [[ -n "$group" ]]; do
    [[ -z "$group" ]] && continue

    if jq -e --arg g "$group" 'select(.group==$g and .status=="GROUP_COMPLETED")' "$jobs_file" >/dev/null 2>&1; then
      _ui_prefix "Group $group" "Skipping: already completed in prior run stage"
      _progress_event "$status_dir" "$run_id" "$group" "" "plan" "GROUP_SKIPPED" "info" "group already completed; skipping"
      continue
    fi

    local group_mode group_counts
    local group_start_sha group_start_ts
    group_start_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "")
    group_start_ts=$(date +%s 2>/dev/null || echo 0)
    group_mode=$(ui_detect_group_mode "$workspace" "$group" "3")
    group_counts=$(ui_print_group_plan "$workspace" "$group" "0" | tail -n 1)
    ui_print_group_start "$group" "$group_mode" "$group_counts"
    local group_pending_for_slots
    group_pending_for_slots=$(echo "$group_counts" | jq -r '.pending // 0')
    [[ "$group_pending_for_slots" -gt 3 ]] && group_pending_for_slots=3
    [[ "$group_pending_for_slots" -lt 1 ]] && group_pending_for_slots=1
    ui_slots_init "$group_pending_for_slots" "$run_id" "$status_dir"
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_STARTED"

    local integration_pair
    integration_pair=$(create_group_integration_worktree "$workspace" "$run_id" "$group" "$base_ref" "$status_dir")
    local integration_branch integration_dir
    integration_branch=$(echo "$integration_pair" | cut -d'|' -f1)
    integration_dir=$(echo "$integration_pair" | cut -d'|' -f2)

    local success_lines
    success_lines=$(collect_group_success_branches "$workspace" "$run_id" "$group" "$status_dir" || true)
    while IFS='|' read -r task_id branch _wt_dir _sha || [[ -n "$task_id" ]]; do
      [[ -z "$task_id" ]] && continue
      local slot_id
      slot_id=$(ui_slot_acquire)
      if [[ "$slot_id" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
        ui_slot_bind "$slot_id" "$task_id" "$group" "$task_id"
      fi
      if ! _merge_success_branch "$integration_dir" "$branch" "$status_dir/merge.log"; then
        if ! _auto_resolve_merge_conflict "$workspace" "$run_id" "$integration_branch" "$task_id" "$branch"; then
          ui_print_group_task_result "$task_id" "failed" "resume_merge_failed"
          if [[ "${slot_id:-0}" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
            ui_slot_release "$slot_id" "failed"
          fi
          ui_slots_stop
          record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_FAILED" '{"reason":"resume_merge_failed"}'
          _progress_event "$status_dir" "$run_id" "$group" "$task_id" "task" "TASK_RESULT" "error" "resume merge failed"
          return 1
        fi
        integration_dir="$(_create_integration_worktree "$workspace" "$run_id" "$integration_branch" "$integration_branch")"
      fi
      ui_print_group_task_result "$task_id" "merged"
      if [[ "${slot_id:-0}" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
        ui_slot_release "$slot_id" "merged"
      fi
      _mark_task_complete_in_integration "$integration_dir" "$task_id" "$status_dir/merge.log"
    done <<<"$success_lines"

    record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_TASKS_DONE"
    if ! orchestrate_group_parallel "$workspace" "$run_id" "$group" "$base_ref" "$integration_branch" "$status_dir"; then
      ui_slots_stop
      _ui_prefix "Group $group" "Stopped: orchestrator failed (fail-closed)"
      record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_FAILED" '{"reason":"orchestrator_failed"}'
      return 1
    fi
    ui_slots_stop
    ui_print_group_done "$group" '{"merged":0,"failed":0,"blocked":0}'
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_COMPLETED"
    local group_elapsed
    group_elapsed=$(( $(date +%s 2>/dev/null || echo 0) - group_start_ts ))
    summarize_group_changes_with_agent "$workspace" "$run_id" "$group" "$status_dir" "$group_start_sha" "$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "$group_start_sha")" "$group_elapsed"
  done <<<"$groups"

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "RESUME_DONE" '{ts:now|todateiso8601, run_id:$run_id, status:$status}'
  _progress_event "$status_dir" "$run_id" "" "" "summary" "RUN_DONE" "info" "resume completed"
  local resume_head_sha resume_elapsed
  resume_head_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "$resume_start_sha")
  resume_elapsed=$(( $(date +%s 2>/dev/null || echo 0) - resume_start_ts ))
  summarize_run_changes_with_agent "$workspace" "$run_id" "$status_dir" "$resume_start_sha" "$resume_head_sha" "$resume_elapsed"
  return 0
}

repair_parallel_run() {
  local workspace="$1"
  local run_id="$2"

  local status_dir
  status_dir="$(ralphex_state_dir "$workspace")/parallel/$run_id"
  local jobs_file="$status_dir/jobs.jsonl"
  local meta="$status_dir/run.meta"

  [[ -f "$meta" ]] || { echo "No run meta found for run_id=$run_id" >&2; return 1; }

  local base_ref
  base_ref=$(awk -F= '$1=="base_ref"{print $2}' "$meta")
  [[ -n "$base_ref" ]] || base_ref="main"

  _ui_prefix "Plan" "Repairing run $run_id from first incomplete group barrier"
  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "REPAIR_STARTED" '{ts:now|todateiso8601, run_id:$run_id, status:$status}'
  _progress_event "$status_dir" "$run_id" "" "" "plan" "RUN_REPAIRED" "info" "repair started"
  local repair_start_sha repair_start_ts
  repair_start_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "")
  repair_start_ts=$(date +%s 2>/dev/null || echo 0)

  local groups
  groups=$(get_pending_groups "$workspace" || true)
  local group
  while IFS= read -r group || [[ -n "$group" ]]; do
    [[ -z "$group" ]] && continue
    if jq -e --arg g "$group" 'select(.group==$g and .status=="GROUP_COMPLETED")' "$jobs_file" >/dev/null 2>&1; then
      _ui_prefix "Group $group" "Skipping: already completed"
      _progress_event "$status_dir" "$run_id" "$group" "" "plan" "GROUP_SKIPPED" "info" "group already completed; skipping"
      continue
    fi

    local group_mode group_counts
    local group_start_sha group_start_ts
    group_start_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "")
    group_start_ts=$(date +%s 2>/dev/null || echo 0)
    group_mode=$(ui_detect_group_mode "$workspace" "$group" "3")
    group_counts=$(ui_print_group_plan "$workspace" "$group" "0" | tail -n 1)
    ui_print_group_start "$group" "$group_mode" "$group_counts"
    ui_slots_init 1 "$run_id" "$status_dir"

    local integration_pair
    integration_pair=$(create_group_integration_worktree "$workspace" "$run_id" "$group" "$base_ref" "$status_dir")
    local integration_branch
    integration_branch=$(echo "$integration_pair" | cut -d'|' -f1)

    if orchestrate_group_parallel "$workspace" "$run_id" "$group" "$base_ref" "$integration_branch" "$status_dir"; then
      ui_slots_stop
      ui_print_group_done "$group" '{"merged":0,"failed":0,"blocked":0}'
      record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_COMPLETED"
      local group_elapsed
      group_elapsed=$(( $(date +%s 2>/dev/null || echo 0) - group_start_ts ))
      summarize_group_changes_with_agent "$workspace" "$run_id" "$group" "$status_dir" "$group_start_sha" "$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "$group_start_sha")" "$group_elapsed"
    else
      ui_slots_stop
      _ui_prefix "Group $group" "Stopped: repair orchestrator failed"
      record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_FAILED" '{"reason":"repair_orchestrator_failed"}'
      _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "REPAIR_FAILED" '{ts:now|todateiso8601, run_id:$run_id, status:$status}'
      _progress_event "$status_dir" "$run_id" "$group" "" "summary" "RUN_DONE" "error" "repair failed"
      return 1
    fi
  done <<<"$groups"

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "REPAIR_DONE" '{ts:now|todateiso8601, run_id:$run_id, status:$status}'
  _progress_event "$status_dir" "$run_id" "" "" "summary" "RUN_DONE" "info" "repair completed"
  local repair_head_sha repair_elapsed
  repair_head_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "$repair_start_sha")
  repair_elapsed=$(( $(date +%s 2>/dev/null || echo 0) - repair_start_ts ))
  summarize_run_changes_with_agent "$workspace" "$run_id" "$status_dir" "$repair_start_sha" "$repair_head_sha" "$repair_elapsed"
  return 0
}

run_parallel_tasks() {
  local workspace="$1"
  local max_parallel="${2:-3}"
  local integration_branch_unused="${3:-}"
  local max_tasks="${4:-0}"
  local run_id="${5:-}"
  local base_ref="${6:-main}"

  if ! git -C "$workspace" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Parallel mode requires a git repository." >&2
    return 1
  fi

  [[ -n "$run_id" ]] || run_id=$(date '+%Y%m%d%H%M%S')

  local status_dir
  status_dir="$(ralphex_state_dir "$workspace")/parallel/$run_id"
  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"
  local progress_file="$status_dir/progress.jsonl"
  mkdir -p "$status_dir"
  touch "$progress_file"

  local run_start_sha run_start_ts
  run_start_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "")
  run_start_ts=$(date +%s 2>/dev/null || echo 0)

  {
    echo "run_id=$run_id"
    echo "base_ref=$base_ref"
    echo "orchestrator_mode=group-barrier"
    echo "commit_style=group-checkpoint"
    echo "failure_mode=fail-closed"
    echo "created_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "runner_version=$(git -C "$workspace" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  } >"$status_dir/run.meta"

  init_ralphex_dir "$workspace"
  log_activity "$workspace" "parallel run start: run_id=$run_id base=$base_ref orchestrator=group-barrier"

  local execution_mode="parallel"
  [[ "$max_parallel" -le 1 ]] && execution_mode="sequential"
  _ui_prefix "Plan" "Execution mode selected: $execution_mode"
  _ui_prefix "Plan" "Run stream started (run_id=$run_id)"
  _progress_event "$status_dir" "$run_id" "" "" "plan" "RUN_PLAN_READY" "info" "run plan ready" "$(jq -nc --arg mode "$execution_mode" --arg base_ref "$base_ref" --arg max_parallel "$max_parallel" --arg max_tasks "$max_tasks" '{mode:$mode,base_ref:$base_ref,max_parallel:($max_parallel|tonumber),max_tasks:($max_tasks|tonumber)}')"

  local groups
  groups=$(get_pending_groups "$workspace" || true)
  if [[ -z "$groups" ]]; then
    _ui_prefix "Summary" "No pending tasks. Nothing to execute."
    return 0
  fi

  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg base_ref "$base_ref" --arg status "RUN_STARTED" '{ts:now|todateiso8601, run_id:$run_id, base_ref:$base_ref, status:$status}'
  _progress_event "$status_dir" "$run_id" "" "" "plan" "RUN_STARTED" "info" "run started" "$(jq -nc --arg base_ref "$base_ref" --arg mode "$execution_mode" '{base_ref:$base_ref,mode:$mode}')"

  local merged_count=0
  local failed_count=0
  local blocked_count=0
  local launched_tasks=0
  local stop=0
  local job_global=0
  local fatal=0
  local groups_completed=0
  local groups_failed=0
  local orchestrator_attempted=0
  local orchestrator_succeeded=0
  local orchestrator_failed=0

  local inventory_json
  inventory_json=$(get_inventory_counts "$workspace")
  local tasks_skipped_initial groups_skipped_initial
  tasks_skipped_initial=$(echo "$inventory_json" | jq -r '.completed_tasks')
  groups_skipped_initial=$(( $(echo "$inventory_json" | jq -r '.total_groups') - $(echo "$inventory_json" | jq -r '.pending_groups') ))

  _required_tools_missing() {
    local csv="${1:-}"
    [[ -z "$csv" ]] && return 1
    local missing=""
    local tool
    for tool in $(echo "$csv" | tr ',' ' '); do
      [[ -z "$tool" ]] && continue
      if ! command -v "$tool" >/dev/null 2>&1; then
        missing="${missing}${missing:+,}$tool"
      fi
    done
    if [[ -n "$missing" ]]; then
      echo "$missing"
      return 0
    fi
    return 1
  }

  _deps_satisfied() {
    local integration_dir="$1"
    local deps_csv="${2:-}"
    [[ -z "$deps_csv" ]] && return 0
    local dep
    for dep in $(echo "$deps_csv" | tr ',' ' '); do
      [[ -z "$dep" ]] && continue
      local row
      row=$(get_task_by_id "$integration_dir" "$dep" || true)
      local st
      st=$(echo "$row" | cut -d'|' -f2)
      if [[ "$st" != "completed" ]]; then
        return 1
      fi
    done
    return 0
  }

  while IFS= read -r group || [[ -n "$group" ]]; do
    if [[ "$stop" -eq 1 ]]; then
      break
    fi
    [[ -z "$group" ]] && continue

    local group_start_sha group_start_ts
    group_start_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "")
    group_start_ts=$(date +%s 2>/dev/null || echo 0)

    local group_mode group_counts
    group_mode=$(ui_detect_group_mode "$workspace" "$group" "$max_parallel")
    group_counts=$(ui_print_group_plan "$workspace" "$group" "0" | tail -n 1)
    ui_print_group_start "$group" "$group_mode" "$group_counts"
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_STARTED"

    local integration_pair
    integration_pair=$(create_group_integration_worktree "$workspace" "$run_id" "$group" "$base_ref" "$status_dir")
    local integration_branch integration_dir
    integration_branch=$(echo "$integration_pair" | cut -d'|' -f1)
    integration_dir=$(echo "$integration_pair" | cut -d'|' -f2)

    local blocked_file="$status_dir/blocked_tasks.txt"
    local slot_map_file="$status_dir/group-${group}.slots"
    : >"$slot_map_file"
    touch "$blocked_file"
    local merged_in_group=0
    local blocked_in_group=0
    local group_progress_cursor=0
    local group_pending_for_slots
    group_pending_for_slots=$(echo "$group_counts" | jq -r '.pending // 0')
    if [[ "$group_pending_for_slots" -gt "$max_parallel" ]]; then
      group_pending_for_slots="$max_parallel"
    fi
    if [[ "$group_pending_for_slots" -lt 1 ]]; then
      group_pending_for_slots=1
    fi
    ui_slots_init "$group_pending_for_slots" "$run_id" "$status_dir"

    while true; do
      if [[ "$fatal" -eq 1 || "$stop" -eq 1 ]]; then
        break
      fi

      local pending
      pending=$(get_tasks_by_group "$integration_dir" "$group" || true)
      [[ -z "$pending" ]] && break

      local ready_lines=""
      local seq_line=""

      while IFS='|' read -r task_id status group_num task_desc line_no tools test_cmd seq deps || [[ -n "$task_id" ]]; do
        [[ -z "$task_id" ]] && continue

        if grep -Fqx "$task_id" "$blocked_file" 2>/dev/null; then
          continue
        fi

        if ! _deps_satisfied "$integration_dir" "$deps"; then
          continue
        fi

        local missing
        missing=$(_required_tools_missing "$tools" || true)
        if [[ -n "$missing" ]]; then
          echo "$task_id" >>"$blocked_file"
          echo "$task_id blocked: missing_tool:$missing" >>"$status_dir/failures.log"
          _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg status "JOB_FAILED" --arg reason "missing_tool:$missing" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, status:$status, reason:$reason}'
          log_error "$workspace" "Task $task_id blocked (missing tool): $missing"
          log_progress "$workspace" "Blocked $task_id in group $group: missing_tool:$missing"
          blocked_count=$((blocked_count + 1))
          blocked_in_group=$((blocked_in_group + 1))
          ui_print_group_task_result "$task_id" "blocked" "missing_tool:$missing"
          _progress_event "$status_dir" "$run_id" "$group" "$task_id" "task" "TASK_RESULT" "warn" "task blocked: missing tool $missing" "$(jq -nc --arg reason "missing_tool:$missing" '{reason:$reason}')"
          continue
        fi

        if [[ "$seq" == "true" && -z "$seq_line" ]]; then
          seq_line="$task_id|$task_desc|$line_no|$tools|$test_cmd"
        else
          ready_lines="${ready_lines}${ready_lines:+$'\n'}$task_id|$task_desc|$line_no|$tools|$test_cmd"
        fi
      done <<<"$pending"

      local to_launch=""
      local launch_parallel="$max_parallel"
      if [[ -n "$seq_line" ]]; then
        to_launch="$seq_line"
        launch_parallel=1
      else
        to_launch="$ready_lines"
      fi

      [[ -z "$to_launch" ]] && break

      local pids=""
      local group_status_files=""
      local launched_in_batch=0

      while IFS='|' read -r task_id task_desc line_no tools test_cmd || [[ -n "$task_id" ]]; do
        [[ -z "$task_id" ]] && continue

        job_global=$((job_global + 1))
        group_status_files="$group_status_files $status_dir/job-$job_global.status"
        local slot_id slot_start_ts
        slot_id=$(ui_slot_acquire)
        if [[ "$slot_id" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
          ui_slot_bind "$slot_id" "$task_id" "$group" "$task_id"
          slot_start_ts=$(date +%s 2>/dev/null || echo 0)
          _slot_map_add "$slot_map_file" "$task_id" "$slot_id" "$slot_start_ts"
        else
          slot_id=0
        fi

        _run_agent_in_worktree "$workspace" "$run_id" "$integration_branch" "$task_id" "$task_desc" "$line_no" "$job_global" "$tools" "$test_cmd" "$slot_id" &
        pids="$pids $!"

        launched_tasks=$((launched_tasks + 1))
        launched_in_batch=$((launched_in_batch + 1))

        if [[ "$max_tasks" -gt 0 ]] && [[ "$launched_tasks" -ge "$max_tasks" ]]; then
          stop=1
        fi

        if [[ "$launched_in_batch" -ge "$launch_parallel" ]]; then
          break
        fi
      done <<<"$to_launch"

      while true; do
        local alive=0
        local pid
        for pid in $pids; do
          if kill -0 "$pid" >/dev/null 2>&1; then
            alive=1
            break
          fi
        done
        group_progress_cursor=$(_sync_group_slot_updates "$progress_file" "$group_progress_cursor" "$slot_map_file" "$group")
        if [[ "$alive" -eq 0 ]]; then
          break
        fi
        sleep 1
      done
      for pid in $pids; do wait "$pid"; done
      group_progress_cursor=$(_sync_group_slot_updates "$progress_file" "$group_progress_cursor" "$slot_map_file" "$group")

      local status_file
      for status_file in $group_status_files; do
        [[ -f "$status_file" ]] || continue
        local outcome task_id branch wt_dir reason sha tools test_cmd slot_id
        IFS='|' read -r outcome task_id branch wt_dir reason sha tools test_cmd slot_id < "$status_file"

        if [[ "$outcome" == "SUCCESS" ]]; then
          if _merge_success_branch "$integration_dir" "$branch" "$merge_log"; then
            _mark_task_complete_in_integration "$integration_dir" "$task_id" "$merge_log"
            _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "MERGED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
            _progress_event "$status_dir" "$run_id" "$group" "$task_id" "task" "TASK_RESULT" "info" "task merged to integration" "$(jq -nc --arg branch "$branch" '{branch:$branch,result:"merged"}')"
            merged_count=$((merged_count + 1))
            merged_in_group=$((merged_in_group + 1))
            log_progress "$workspace" "Merged $task_id into $integration_branch (group $group)."
            ui_print_group_task_result "$task_id" "merged"
            if [[ "${slot_id:-0}" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
              ui_slot_release "$slot_id" "merged"
              _slot_map_remove "$slot_map_file" "$task_id"
            fi
          else
            echo "Merge failed for $branch" >>"$status_dir/merge_failures.log"
            _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "MERGE_FAILED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
            _progress_event "$status_dir" "$run_id" "$group" "$task_id" "task" "TASK_RESULT" "warn" "merge failed; attempting merge-fix" "$(jq -nc --arg branch "$branch" '{branch:$branch,result:"merge_failed"}')"
            if _auto_resolve_merge_conflict "$workspace" "$run_id" "$integration_branch" "$task_id" "$branch"; then
              integration_dir="$(_create_integration_worktree "$workspace" "$run_id" "$integration_branch" "$integration_branch")"
              _mark_task_complete_in_integration "$integration_dir" "$task_id" "$merge_log"
              _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "MERGED" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status}'
              _progress_event "$status_dir" "$run_id" "$group" "$task_id" "task" "TASK_RESULT" "info" "task merged after merge-fix" "$(jq -nc --arg branch "$branch" '{branch:$branch,result:"merged_after_merge_fix"}')"
              merged_count=$((merged_count + 1))
              merged_in_group=$((merged_in_group + 1))
              log_progress "$workspace" "Auto-resolved merge and merged $task_id into $integration_branch."
              ui_print_group_task_result "$task_id" "merged" "after-merge-fix"
              if [[ "${slot_id:-0}" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
                ui_slot_release "$slot_id" "merged-after-merge-fix"
                _slot_map_remove "$slot_map_file" "$task_id"
              fi
            else
              failed_count=$((failed_count + 1))
              fatal=1
              stop=1
              log_error "$workspace" "Fatal: unresolvable merge for $task_id ($branch)."
              log_progress "$workspace" "Stopped due to unresolvable merge for $task_id ($branch). See $status_dir/merge.log."
              ui_print_group_task_result "$task_id" "failed" "unresolvable_merge"
              _progress_event "$status_dir" "$run_id" "$group" "$task_id" "task" "TASK_RESULT" "error" "task failed: unresolvable merge" "$(jq -nc --arg branch "$branch" '{branch:$branch,reason:"unresolvable_merge"}')"
              if [[ "${slot_id:-0}" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
                ui_slot_release "$slot_id" "failed"
                _slot_map_remove "$slot_map_file" "$task_id"
              fi
            fi
          fi
        else
          echo "$task_id failed: $reason" >>"$status_dir/failures.log"
          _run_log_json "$jobs_file" --arg run_id "$run_id" --arg task_id "$task_id" --arg branch "$branch" --arg status "JOB_FAILED" --arg reason "$reason" '{ts:now|todateiso8601, run_id:$run_id, task_id:$task_id, branch:$branch, status:$status, reason:$reason}'
          failed_count=$((failed_count + 1))
          ui_print_group_task_result "$task_id" "failed" "$reason"
          _progress_event "$status_dir" "$run_id" "$group" "$task_id" "task" "TASK_RESULT" "error" "task failed: $reason" "$(jq -nc --arg reason "$reason" '{reason:$reason}')"
          if [[ "${slot_id:-0}" =~ ^[0-9]+$ ]] && [[ "$slot_id" -gt 0 ]]; then
            ui_slot_release "$slot_id" "failed"
            _slot_map_remove "$slot_map_file" "$task_id"
          fi
        fi

        _cleanup_worktree "$workspace" "$wt_dir"
      done
    done

    ui_slots_stop

    if [[ "$fatal" -eq 1 ]]; then
      groups_failed=$((groups_failed + 1))
      record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_FAILED" '{"reason":"group_task_merge_failed"}'
      _ui_prefix "Group $group" "Stopped: fatal task merge failure before orchestrator."
      local group_elapsed_fail
      group_elapsed_fail=$(( $(date +%s 2>/dev/null || echo 0) - group_start_ts ))
      summarize_group_changes_with_agent "$workspace" "$run_id" "$group" "$status_dir" "$group_start_sha" "$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "$group_start_sha")" "$group_elapsed_fail"
      break
    fi

    record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_TASKS_DONE" "{\"merged\":$merged_in_group}"
    orchestrator_attempted=$((orchestrator_attempted + 1))
    if ! orchestrate_group_parallel "$workspace" "$run_id" "$group" "$base_ref" "$integration_branch" "$status_dir"; then
      failed_count=$((failed_count + 1))
      fatal=1
      stop=1
      groups_failed=$((groups_failed + 1))
      orchestrator_failed=$((orchestrator_failed + 1))
      record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_FAILED" '{"reason":"orchestrator_failed"}'
      log_error "$workspace" "Fatal: orchestrator failed for group $group."
      log_progress "$workspace" "Stopped due to orchestrator failure for group $group."
      _ui_prefix "Group $group" "Stopped: orchestrator failed (fail-closed)."
      break
    fi
    orchestrator_succeeded=$((orchestrator_succeeded + 1))
    groups_completed=$((groups_completed + 1))
    ui_print_group_done "$group" "{\"merged\":$merged_in_group,\"failed\":0,\"blocked\":$blocked_in_group}"
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "GROUP_COMPLETED"
    local group_elapsed
    group_elapsed=$(( $(date +%s 2>/dev/null || echo 0) - group_start_ts ))
    summarize_group_changes_with_agent "$workspace" "$run_id" "$group" "$status_dir" "$group_start_sha" "$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "$group_start_sha")" "$group_elapsed"
  done <<< "$groups"

  _ui_prefix "Summary" "Execution summary: launched=$launched_tasks merged=$merged_count failed=$failed_count blocked=$blocked_count"
  log_activity "$workspace" "parallel run end: run_id=$run_id merged=$merged_count failed=$failed_count"
  _run_log_json "$jobs_file" --arg run_id "$run_id" --arg status "RUN_DONE" --arg merged "$merged_count" --arg failed "$failed_count" '{ts:now|todateiso8601, run_id:$run_id, status:$status, merged:($merged|tonumber), failed:($failed|tonumber)}'

  local head_sha head_branch main_clean next_action
  head_sha=$(git -C "$workspace" rev-parse --short HEAD 2>/dev/null || echo "unknown")
  head_branch=$(git -C "$workspace" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
  if git -C "$workspace" diff --quiet && git -C "$workspace" diff --cached --quiet; then
    main_clean="true"
  else
    main_clean="false"
  fi
  if [[ "$failed_count" -gt 0 ]]; then
    next_action="./ralphex resume --run-id $run_id -y (or ./ralphex repair --run-id $run_id)"
  else
    next_action="run complete"
  fi

  local aggregate_json
  aggregate_json=$(jq -nc \
    --argjson g_completed "$groups_completed" \
    --argjson g_failed "$groups_failed" \
    --argjson g_skipped "$groups_skipped_initial" \
    --argjson t_executed "$launched_tasks" \
    --argjson t_merged "$merged_count" \
    --argjson t_skipped "$tasks_skipped_initial" \
    --argjson t_blocked "$blocked_count" \
    --argjson t_failed "$failed_count" \
    --argjson o_attempted "$orchestrator_attempted" \
    --argjson o_succeeded "$orchestrator_succeeded" \
    --argjson o_failed "$orchestrator_failed" \
    --argjson o_cleanup_ok "$orchestrator_succeeded" \
    --arg head_sha "$head_sha" \
    --arg head_branch "$head_branch" \
    --arg main_clean "$main_clean" \
    --arg next_action "$next_action" \
    '{groups:{completed:$g_completed,failed:$g_failed,skipped:$g_skipped},tasks:{executed:$t_executed,merged:$t_merged,skipped:$t_skipped,blocked:$t_blocked,failed:$t_failed},orchestrator:{attempted:$o_attempted,succeeded:$o_succeeded,failed:$o_failed,cleanup_ok:$o_cleanup_ok},head:{sha:$head_sha,branch:$head_branch,main_clean:$main_clean},next_action:$next_action}')
  ui_print_final_summary "$run_id" "$aggregate_json"
  _progress_event "$status_dir" "$run_id" "" "" "summary" "RUN_SUMMARY" "info" "run summary emitted" "$aggregate_json"
  _progress_event "$status_dir" "$run_id" "" "" "summary" "RUN_DONE" "$([[ "$failed_count" -gt 0 ]] && echo error || echo info)" "run completed"
  local run_head_sha run_elapsed
  run_head_sha=$(git -C "$workspace" rev-parse HEAD 2>/dev/null || echo "$run_start_sha")
  run_elapsed=$(( $(date +%s 2>/dev/null || echo 0) - run_start_ts ))
  summarize_run_changes_with_agent "$workspace" "$run_id" "$status_dir" "$run_start_sha" "$run_head_sha" "$run_elapsed"

  if [[ "$failed_count" -gt 0 ]]; then
    return 1
  fi
  return 0
}

cleanup_parallel_run() {
  local workspace="$1"
  local run_id="$2"
  local _unused_branch="$3"
  rm -rf "$(ralphex_worktrees_dir "$workspace")/$run_id" >/dev/null 2>&1 || true
  rm -rf "$(ralphex_state_dir "$workspace")/integration/$run_id" >/dev/null 2>&1 || true
  rm -rf "$(ralphex_state_dir "$workspace")/merge-fix/$run_id" >/dev/null 2>&1 || true
}
