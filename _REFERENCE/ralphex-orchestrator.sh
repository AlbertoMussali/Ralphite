#!/bin/bash
# Ralphex group-barrier orchestrator

set -euo pipefail

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
source "$SCRIPT_DIR/ralphex-common.sh"
source "$SCRIPT_DIR/ralphex-ui.sh"

record_orchestrator_event() {
  local jobs_file="$1"
  local run_id="$2"
  local group="$3"
  local status="$4"
  local details="${5:-{}}"
  local safe_details="$details"

  # Never let malformed details crash the run lifecycle.
  if ! jq -e . >/dev/null 2>&1 <<<"$details"; then
    safe_details=$(jq -nc --arg details_raw "$details" '{detail_raw:$details_raw}')
  fi

  jq -nc \
    --arg run_id "$run_id" \
    --arg group "$group" \
    --arg status "$status" \
    --argjson details "$safe_details" \
    '{ts:now|todateiso8601, run_id:$run_id, group:$group, status:$status} + $details' >>"$jobs_file"

  local status_dir=""
  status_dir="$(dirname "$jobs_file")"
  local stage="orchestrator"
  local event="$status"
  local level="info"
  local message="$status"
  case "$status" in
    GROUP_STARTED)
      stage="plan"; event="GROUP_STARTED"; message="group started" ;;
    GROUP_TASKS_DONE)
      stage="plan"; event="GROUP_TASKS_DONE"; message="group tasks done" ;;
    GROUP_COMPLETED)
      stage="summary"; event="GROUP_COMPLETED"; message="group completed" ;;
    GROUP_FAILED)
      stage="summary"; event="GROUP_FAILED"; level="error"; message="group failed" ;;
    ORCH_STARTED)
      stage="orchestrator"; event="ORCH_STARTED"; message="orchestrator started" ;;
    ORCH_CONFLICT_RESOLVE_STARTED)
      stage="orchestrator"; event="ORCH_STEP"; message="orchestrator conflict resolve started" ;;
    ORCH_CONFLICT_RESOLVE_FAILED)
      stage="orchestrator"; event="ORCH_FAILED"; level="error"; message="orchestrator conflict resolve failed" ;;
    ORCH_MAIN_COMMIT_OK)
      stage="orchestrator"; event="ORCH_DONE"; message="orchestrator checkpoint committed" ;;
    ORCH_MAIN_COMMIT_FAILED)
      stage="orchestrator"; event="ORCH_FAILED"; level="error"; message="orchestrator checkpoint failed" ;;
    ORCH_CLEANUP_DONE)
      stage="orchestrator"; event="ORCH_DONE"; message="orchestrator cleanup done" ;;
  esac
  ui_emit_standard_event "$status_dir" "$run_id" "$group" "" "$stage" "$event" "$level" "$message" "$safe_details" || true
}

_group_integration_branch() {
  local run_id="$1"
  local group="$2"
  echo "ralphex/integration-${run_id}-g${group}"
}

_group_integration_dir() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  echo "$(ralphex_state_dir "$workspace")/integration/$run_id/g$group"
}

_group_orchestrator_branch() {
  local run_id="$1"
  local group="$2"
  echo "ralphex/orchestrator-${run_id}-g${group}"
}

_group_orchestrator_dir() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  echo "$(ralphex_state_dir "$workspace")/orchestrator/$run_id/g$group"
}

_task_group_matches() {
  local workspace="$1"
  local task_id="$2"
  local expected_group="$3"
  local row
  row=$(get_task_by_id "$workspace" "$task_id" || true)
  local task_group
  task_group=$(echo "$row" | cut -d'|' -f3)
  [[ -n "$task_group" && "$task_group" == "$expected_group" ]]
}

collect_group_success_branches() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local status_dir="$4"

  local status_file
  for status_file in "$status_dir"/job-*.status; do
    [[ -f "$status_file" ]] || continue
    local outcome task_id branch wt_dir reason sha tools test_cmd
    IFS='|' read -r outcome task_id branch wt_dir reason sha tools test_cmd < "$status_file"
    [[ "$outcome" == "SUCCESS" ]] || continue
    _task_group_matches "$workspace" "$task_id" "$group" || continue
    printf '%s|%s|%s|%s\n' "$task_id" "$branch" "$wt_dir" "$sha"
  done
}

create_group_integration_worktree() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local base_ref="$4"
  local status_dir="$5"

  local integration_branch
  integration_branch="$(_group_integration_branch "$run_id" "$group")"
  local integration_dir
  integration_dir="$(_group_integration_dir "$workspace" "$run_id" "$group")"
  local merge_log="$status_dir/merge.log"

  mkdir -p "$(dirname "$integration_dir")"
  if [[ -d "$integration_dir" ]]; then
    echo "$integration_branch|$integration_dir"
    return 0
  fi

  if git -C "$workspace" show-ref --verify --quiet "refs/heads/$integration_branch"; then
    git -C "$workspace" worktree add -f "$integration_dir" "$integration_branch" >>"$merge_log" 2>&1
  else
    git -C "$workspace" worktree add -f -b "$integration_branch" "$integration_dir" "$base_ref" >>"$merge_log" 2>&1
  fi

  echo "$integration_branch|$integration_dir"
}

apply_branch_into_orchestrator_tree() {
  local repo_dir="$1"
  local branch="$2"
  local log_file="$3"

  if git -C "$repo_dir" merge --ff-only "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  if git -C "$repo_dir" -c user.name="ralphex" -c user.email="ralphex@local" merge --no-ff --no-edit "$branch" >>"$log_file" 2>&1; then
    return 0
  fi

  git -C "$repo_dir" merge --abort >/dev/null 2>&1 || true
  return 1
}

resolve_orchestrator_conflicts_with_codex() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local orchestrator_dir="$4"
  local status_dir="$5"

  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"

  ui_print_orchestrator_step "$group" "conflict-resolve" "delegating to codex"
  record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_CONFLICT_RESOLVE_STARTED"

  local conflicts
  conflicts=$(cd "$orchestrator_dir" && git diff --name-only --diff-filter=U || true)

  local agents_md=""
  if [[ -f "$orchestrator_dir/AGENTS.md" ]]; then
    agents_md=$(sed -n '1,240p' "$orchestrator_dir/AGENTS.md")
  fi

  local prompt
  prompt=$(cat <<EOT
You are resolving orchestrator merge conflicts for Ralphex.

Read and follow AGENTS.md first:
----------------
${agents_md:-"(AGENTS.md not found)"}
----------------

Run context:
- run_id: $run_id
- group: $group

Conflict files:
${conflicts:-"(none listed)"}

Rules:
1. Keep compatible behavior from all successful task branches.
2. Preserve repository constraints and existing APIs unless conflicts require a change.
3. Do NOT modify anything under .ralphex/.
4. Resolve conflicts fully so the tree is commit-ready.
5. Print a concise summary and explicitly state if any conflict remains unresolved.
EOT
)

  set +e
  (
    cd "$orchestrator_dir" || exit 1
    codex exec --json --sandbox "$SANDBOX" --model "$MODEL" "$prompt"
  ) 2>&1 \
    | tee -a "$merge_log" \
    | RALPHEX_STREAM_STATUS_DIR="$status_dir" \
      RALPHEX_STREAM_RUN_ID="$run_id" \
      RALPHEX_STREAM_GROUP="$group" \
      RALPHEX_STREAM_STAGE="orchestrator" \
      RALPHEX_REASONING_SUMMARY="${RALPHEX_REASONING_SUMMARY:-1}" \
      "$SCRIPT_DIR/ralphex-stream-parser.sh" "$orchestrator_dir" >/dev/null
  local rc=${PIPESTATUS[0]}
  set -e
  if [[ "$rc" -ne 0 ]]; then
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_CONFLICT_RESOLVE_FAILED" '{"reason":"codex_failed"}'
    return 1
  fi

  # Stage first so codex-resolved files clear unmerged index entries.
  (cd "$orchestrator_dir" && git add -A)
  (cd "$orchestrator_dir" && git reset -q -- RALPHEX_TASK.md RALPH_TASK.md 2>/dev/null || true)
  (cd "$orchestrator_dir" && git reset -q -- .ralphex .ralph 2>/dev/null || true)

  local unresolved
  unresolved=$(cd "$orchestrator_dir" && git diff --name-only --diff-filter=U || true)
  if [[ -n "$unresolved" ]]; then
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_CONFLICT_RESOLVE_FAILED" '{"reason":"unresolved_conflicts"}'
    return 1
  fi

  return 0
}

commit_group_checkpoint_to_main() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local base_branch="$4"
  local integration_branch="$5"
  local status_dir="$6"

  local merge_log="$status_dir/merge.log"
  local jobs_file="$status_dir/jobs.jsonl"
  local orchestrator_branch
  orchestrator_branch="$(_group_orchestrator_branch "$run_id" "$group")"
  local orchestrator_dir
  orchestrator_dir="$(_group_orchestrator_dir "$workspace" "$run_id" "$group")"

  mkdir -p "$(dirname "$orchestrator_dir")"
  ui_print_orchestrator_step "$group" "merge" "squashing $integration_branch into checkpoint worktree"

  if [[ -d "$orchestrator_dir" ]]; then
    git -C "$workspace" worktree remove -f "$orchestrator_dir" >/dev/null 2>&1 || true
  fi

  if git -C "$workspace" show-ref --verify --quiet "refs/heads/$orchestrator_branch"; then
    git -C "$workspace" branch -D "$orchestrator_branch" >/dev/null 2>&1 || true
  fi

  git -C "$workspace" worktree add -f -b "$orchestrator_branch" "$orchestrator_dir" "$base_branch" >>"$merge_log" 2>&1

  set +e
  (cd "$orchestrator_dir" && git merge --squash "$integration_branch") >>"$merge_log" 2>&1
  local merge_rc=$?
  set -e

  if [[ "$merge_rc" -ne 0 ]]; then
    if ! resolve_orchestrator_conflicts_with_codex "$workspace" "$run_id" "$group" "$orchestrator_dir" "$status_dir"; then
      return 1
    fi
  fi

  local unresolved
  unresolved=$(cd "$orchestrator_dir" && git diff --name-only --diff-filter=U || true)
  [[ -z "$unresolved" ]] || return 1

  local success_lines
  success_lines=$(collect_group_success_branches "$workspace" "$run_id" "$group" "$status_dir" || true)
  local task_count=0
  local task_file_rel="RALPHEX_TASK.md"
  [[ -f "$orchestrator_dir/$task_file_rel" ]] || task_file_rel="RALPH_TASK.md"

  while IFS='|' read -r task_id _rest || [[ -n "$task_id" ]]; do
    [[ -z "$task_id" ]] && continue
    mark_task_complete "$orchestrator_dir" "$task_id" || true
    task_count=$((task_count + 1))
  done <<<"$success_lines"

  (cd "$orchestrator_dir" && git add -A)
  if (cd "$orchestrator_dir" && git diff --cached --quiet); then
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_MAIN_COMMIT_OK" '{"checkpoint_commit":"noop"}'
    git -C "$workspace" worktree remove -f "$orchestrator_dir" >/dev/null 2>&1 || true
    git -C "$workspace" branch -D "$orchestrator_branch" >/dev/null 2>&1 || true
    return 0
  fi

  local message
  message="ralphex: group ${group} checkpoint (${task_count} tasks)"
  (cd "$orchestrator_dir" && git -c user.name="ralphex" -c user.email="ralphex@local" commit -m "$message") >>"$merge_log" 2>&1
  local commit_sha
  commit_sha=$(cd "$orchestrator_dir" && git rev-parse --short HEAD)

  ui_print_orchestrator_step "$group" "checkpoint" "fast-forwarding $base_branch"
  git -C "$workspace" checkout "$base_branch" >/dev/null 2>&1 || true
  if ! git -C "$workspace" merge --ff-only "$orchestrator_branch" >>"$merge_log" 2>&1; then
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_MAIN_COMMIT_FAILED" '{"reason":"ff_to_main_failed"}'
    return 1
  fi

  record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_MAIN_COMMIT_OK" "{\"checkpoint_commit\":\"$commit_sha\"}"

  git -C "$workspace" worktree remove -f "$orchestrator_dir" >/dev/null 2>&1 || true
  git -C "$workspace" branch -D "$orchestrator_branch" >/dev/null 2>&1 || true
  ui_print_orchestrator_done "$group" "checkpointed" "$commit_sha"
  return 0
}

cleanup_group_artifacts() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local integration_branch="$4"
  local status_dir="$5"

  local jobs_file="$status_dir/jobs.jsonl"
  local success_lines
  success_lines=$(collect_group_success_branches "$workspace" "$run_id" "$group" "$status_dir" || true)
  ui_print_orchestrator_step "$group" "cleanup" "removing task/integration worktrees and branches"

  while IFS='|' read -r task_id branch wt_dir _sha || [[ -n "$task_id" ]]; do
    [[ -z "$task_id" ]] && continue
    git -C "$workspace" worktree remove -f "$wt_dir" >/dev/null 2>&1 || true
    rm -rf "$wt_dir" >/dev/null 2>&1 || true
    git -C "$workspace" branch -D "$branch" >/dev/null 2>&1 || true
    git -C "$workspace" branch -D "ralphex/mergefix-${run_id}-${task_id}" >/dev/null 2>&1 || true
    rm -rf "$(ralphex_state_dir "$workspace")/merge-fix/$run_id/$task_id" >/dev/null 2>&1 || true
  done <<<"$success_lines"

  local integration_dir
  integration_dir="$(_group_integration_dir "$workspace" "$run_id" "$group")"
  git -C "$workspace" worktree remove -f "$integration_dir" >/dev/null 2>&1 || true
  rm -rf "$integration_dir" >/dev/null 2>&1 || true
  git -C "$workspace" branch -D "$integration_branch" >/dev/null 2>&1 || true

  record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_CLEANUP_DONE"
}

orchestrate_group_parallel() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local base_branch="$4"
  local integration_branch="$5"
  local status_dir="$6"

  local jobs_file="$status_dir/jobs.jsonl"
  ui_print_orchestrator_start "$group"
  record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_STARTED"

  if ! git -C "$workspace" show-ref --verify --quiet "refs/heads/$integration_branch"; then
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_MAIN_COMMIT_FAILED" '{"reason":"integration_branch_missing"}'
    return 1
  fi

  if ! commit_group_checkpoint_to_main "$workspace" "$run_id" "$group" "$base_branch" "$integration_branch" "$status_dir"; then
    record_orchestrator_event "$jobs_file" "$run_id" "$group" "ORCH_MAIN_COMMIT_FAILED" '{"reason":"checkpoint_failed"}'
    return 1
  fi

  cleanup_group_artifacts "$workspace" "$run_id" "$group" "$integration_branch" "$status_dir"
  ui_print_orchestrator_done "$group" "cleanup-complete"
  return 0
}

orchestrate_group_sequential() {
  local workspace="$1"
  local run_id="$2"
  local group="$3"
  local base_branch="$4"
  local status_dir="$5"
  local integration_branch
  integration_branch="$(_group_integration_branch "$run_id" "$group")"
  orchestrate_group_parallel "$workspace" "$run_id" "$group" "$base_branch" "$integration_branch" "$status_dir"
}
