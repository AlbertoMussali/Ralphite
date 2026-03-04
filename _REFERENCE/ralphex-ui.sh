#!/bin/bash
# Ralphex CLI presentation helpers

set -euo pipefail

if [[ -n "${RALPHEX_UI_SH_LOADED:-}" ]]; then
  return 0
fi
RALPHEX_UI_SH_LOADED=1

RALPHEX_LIVE_PROGRESS="${RALPHEX_LIVE_PROGRESS:-1}"
RALPHEX_NO_LIVE="${RALPHEX_NO_LIVE:-0}"
RALPHEX_REASONING_SUMMARY="${RALPHEX_REASONING_SUMMARY:-1}"
RALPHEX_LIVE_SLOTS="${RALPHEX_LIVE_SLOTS:-1}"

UI_LIVE_ENABLED=0
UI_LIVE_LAST_LEN=0
UI_LIVE_ACTIVE=0
UI_LIVE_WIDTH=120
UI_LIVE_LAST_LINE=""
UI_LIVE_START_TS=0

UI_SLOTS_ENABLED=0
UI_SLOTS_COUNT=0
UI_SLOTS_RENDERED=0
UI_SLOTS_LAST_RENDER_SEC=0
UI_SLOTS_MIN_RENDER_INTERVAL_SEC=1
declare -a UI_SLOT_GROUP
declare -a UI_SLOT_TASK
declare -a UI_SLOT_LABEL
declare -a UI_SLOT_PHASE
declare -a UI_SLOT_MSG
declare -a UI_SLOT_LEVEL
declare -a UI_SLOT_ELAPSED

_ui_now_iso() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

_ui_sanitize_text() {
  local input="${1:-}"
  local max_len="${2:-300}"
  local out
  out=$(printf '%s' "$input" | tr '\r\n' ' ' | tr -cd '\11\12\15\40-\176')
  out=$(printf '%s' "$out" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
  if [[ ${#out} -gt "$max_len" ]]; then
    out="${out:0:$((max_len-3))}..."
  fi
  printf '%s' "$out"
}

ui_emit_event() {
  local status_dir="$1"
  local event_json="$2"
  [[ -n "${status_dir:-}" ]] || return 0
  mkdir -p "$status_dir" >/dev/null 2>&1 || true
  local progress_file="$status_dir/progress.jsonl"
  local normalized
  normalized=$(jq -c '
    {
      ts: (.ts // (now|todateiso8601)),
      run_id: (.run_id // ""),
      group: (.group // null),
      task_id: (.task_id // null),
      stage: (.stage // "plan"),
      event: (.event // "UNSPECIFIED"),
      level: (.level // "info"),
      message: ((.message // "") | tostring),
      meta: (if (.meta|type)=="object" then .meta else {} end)
    }' <<<"$event_json" 2>/dev/null || true)
  [[ -n "$normalized" ]] || return 0
  printf '%s\n' "$normalized" >>"$progress_file" 2>/dev/null || true
}

ui_emit_standard_event() {
  local status_dir="$1"
  local run_id="$2"
  local group="${3:-}"
  local task_id="${4:-}"
  local stage="$5"
  local event="$6"
  local level="$7"
  local message="$8"
  local meta_json="${9:-{}}"

  if ! jq -e . >/dev/null 2>&1 <<<"$meta_json"; then
    meta_json=$(jq -nc --arg raw "$meta_json" '{detail_raw:$raw}')
  fi

  local payload
  payload=$(jq -nc \
    --arg ts "$(_ui_now_iso)" \
    --arg run_id "$run_id" \
    --arg group "$group" \
    --arg task_id "$task_id" \
    --arg stage "$stage" \
    --arg event "$event" \
    --arg level "$level" \
    --arg message "$(_ui_sanitize_text "$message" 300)" \
    --argjson meta "$meta_json" \
    '{ts:$ts,run_id:$run_id,group:($group|if .=="" then null else . end),task_id:($task_id|if .=="" then null else . end),stage:$stage,event:$event,level:$level,message:$message,meta:$meta}')
  ui_emit_event "$status_dir" "$payload"
}

ui_live_init() {
  if [[ "$RALPHEX_LIVE_PROGRESS" != "1" || "$RALPHEX_NO_LIVE" == "1" ]]; then
    UI_LIVE_ENABLED=0
    return 0
  fi
  if [[ ! -t 1 || "${TERM:-dumb}" == "dumb" ]]; then
    UI_LIVE_ENABLED=0
    return 0
  fi
  UI_LIVE_ENABLED=1
  UI_LIVE_LAST_LEN=0
  UI_LIVE_ACTIVE=0
  UI_LIVE_START_TS=$(date +%s 2>/dev/null || echo 0)
  UI_LIVE_WIDTH=$(tput cols 2>/dev/null || echo 120)
  [[ "$UI_LIVE_WIDTH" -gt 0 ]] || UI_LIVE_WIDTH=120
}

ui_live_flush_line() {
  if [[ "$UI_LIVE_ENABLED" -eq 1 && "$UI_LIVE_ACTIVE" -eq 1 ]]; then
    printf '\r%*s\r' "$UI_LIVE_LAST_LEN" ''
    printf '%s\n' "$UI_LIVE_LAST_LINE"
    UI_LIVE_ACTIVE=0
  fi
}

ui_live_update() {
  local stage="$1"
  local text="$2"
  local _level="${3:-info}"
  if [[ "$UI_LIVE_ENABLED" -ne 1 ]]; then
    return 0
  fi
  local now elapsed line max_width
  now=$(date +%s 2>/dev/null || echo 0)
  elapsed=$((now - UI_LIVE_START_TS))
  (( elapsed < 0 )) && elapsed=0
  line="[Live] ${stage} | $(_ui_sanitize_text "$text" 220) | $(printf '%02d:%02d' $((elapsed/60)) $((elapsed%60))) elapsed"
  max_width=$((UI_LIVE_WIDTH - 1))
  (( max_width < 40 )) && max_width=40
  if [[ ${#line} -gt "$max_width" ]]; then
    line="${line:0:$((max_width-3))}..."
  fi
  local pad=$((UI_LIVE_LAST_LEN - ${#line}))
  (( pad < 0 )) && pad=0
  printf '\r%s%*s' "$line" "$pad" ''
  UI_LIVE_LAST_LEN=${#line}
  UI_LIVE_LAST_LINE="$line"
  UI_LIVE_ACTIVE=1
}

ui_live_stop() {
  if [[ "$UI_LIVE_ENABLED" -eq 1 ]]; then
    if [[ "$UI_LIVE_ACTIVE" -eq 1 ]]; then
      printf '\r%*s\r' "$UI_LIVE_LAST_LEN" ''
      printf '%s\n' "$UI_LIVE_LAST_LINE"
    fi
  fi
  UI_LIVE_ACTIVE=0
  UI_LIVE_LAST_LEN=0
}

ui_slots_init() {
  local max_slots="${1:-0}"
  local _run_id="${2:-}"
  local _status_dir="${3:-}"
  UI_SLOTS_ENABLED=0
  UI_SLOTS_COUNT=0
  UI_SLOTS_RENDERED=0
  UI_SLOTS_LAST_RENDER_SEC=0

  if [[ "$RALPHEX_LIVE_SLOTS" != "1" || "$RALPHEX_LIVE_PROGRESS" != "1" || "$RALPHEX_NO_LIVE" == "1" ]]; then
    return 0
  fi
  if [[ ! -t 1 || "${TERM:-dumb}" == "dumb" ]]; then
    return 0
  fi
  [[ "$max_slots" =~ ^[0-9]+$ ]] || max_slots=0
  if [[ "$max_slots" -le 0 ]]; then
    return 0
  fi

  UI_SLOTS_ENABLED=1
  UI_SLOTS_COUNT="$max_slots"
  local i
  for ((i=1; i<=UI_SLOTS_COUNT; i++)); do
    UI_SLOT_GROUP[$i]="-"
    UI_SLOT_TASK[$i]="-"
    UI_SLOT_LABEL[$i]="idle"
    UI_SLOT_PHASE[$i]="idle"
    UI_SLOT_MSG[$i]="waiting"
    UI_SLOT_LEVEL[$i]="info"
    UI_SLOT_ELAPSED[$i]=0
  done
  ui_slots_render force
}

ui_slot_acquire() {
  [[ "$UI_SLOTS_ENABLED" -eq 1 ]] || { echo "0"; return 0; }
  local i
  for ((i=1; i<=UI_SLOTS_COUNT; i++)); do
    if [[ "${UI_SLOT_TASK[$i]:--}" == "-" ]]; then
      echo "$i"
      return 0
    fi
  done
  echo "0"
}

ui_slot_bind() {
  local slot_id="$1"
  local task_id="$2"
  local group="$3"
  local label="$4"
  [[ "$UI_SLOTS_ENABLED" -eq 1 ]] || return 0
  [[ "$slot_id" =~ ^[0-9]+$ ]] || return 0
  if [[ "$slot_id" -lt 1 || "$slot_id" -gt "$UI_SLOTS_COUNT" ]]; then
    return 0
  fi
  UI_SLOT_GROUP[$slot_id]="${group:--}"
  UI_SLOT_TASK[$slot_id]="${task_id:--}"
  UI_SLOT_LABEL[$slot_id]="${label:-$task_id}"
  UI_SLOT_PHASE[$slot_id]="running"
  UI_SLOT_MSG[$slot_id]="starting"
  UI_SLOT_LEVEL[$slot_id]="info"
  UI_SLOT_ELAPSED[$slot_id]=0
  ui_slots_render
}

ui_slot_update() {
  local slot_id="$1"
  local phase="$2"
  local message="$3"
  local level="${4:-info}"
  local elapsed_secs="${5:-0}"
  [[ "$UI_SLOTS_ENABLED" -eq 1 ]] || return 0
  [[ "$slot_id" =~ ^[0-9]+$ ]] || return 0
  if [[ "$slot_id" -lt 1 || "$slot_id" -gt "$UI_SLOTS_COUNT" ]]; then
    return 0
  fi
  UI_SLOT_PHASE[$slot_id]="$(_ui_sanitize_text "${phase:-running}" 24)"
  UI_SLOT_MSG[$slot_id]="$(_ui_sanitize_text "${message:-working}" 120)"
  UI_SLOT_LEVEL[$slot_id]="$(_ui_sanitize_text "${level:-info}" 10)"
  if [[ "$elapsed_secs" =~ ^[0-9]+$ ]]; then
    UI_SLOT_ELAPSED[$slot_id]="$elapsed_secs"
  fi
  ui_slots_render
}

ui_slot_release() {
  local slot_id="$1"
  local result="${2:-done}"
  [[ "$UI_SLOTS_ENABLED" -eq 1 ]] || return 0
  [[ "$slot_id" =~ ^[0-9]+$ ]] || return 0
  if [[ "$slot_id" -lt 1 || "$slot_id" -gt "$UI_SLOTS_COUNT" ]]; then
    return 0
  fi
  UI_SLOT_PHASE[$slot_id]="done"
  UI_SLOT_MSG[$slot_id]="$(_ui_sanitize_text "$result" 120)"
  UI_SLOT_LEVEL[$slot_id]="info"
  UI_SLOT_TASK[$slot_id]="-"
  UI_SLOT_LABEL[$slot_id]="idle"
  UI_SLOT_GROUP[$slot_id]="-"
  UI_SLOT_ELAPSED[$slot_id]=0
  ui_slots_render force
}

ui_slots_render() {
  local mode="${1:-normal}"
  [[ "$UI_SLOTS_ENABLED" -eq 1 ]] || return 0
  local now
  now=$(date +%s 2>/dev/null || echo 0)
  if [[ "$mode" != "force" && "$UI_SLOTS_LAST_RENDER_SEC" -gt 0 ]]; then
    if [[ $((now - UI_SLOTS_LAST_RENDER_SEC)) -lt "$UI_SLOTS_MIN_RENDER_INTERVAL_SEC" ]]; then
      return 0
    fi
  fi

  if [[ "$UI_SLOTS_RENDERED" -eq 1 ]]; then
    printf '\033[%sA' "$UI_SLOTS_COUNT"
  fi
  local i
  for ((i=1; i<=UI_SLOTS_COUNT; i++)); do
    local elapsed="${UI_SLOT_ELAPSED[$i]:-0}"
    [[ "$elapsed" =~ ^[0-9]+$ ]] || elapsed=0
    local elapsed_fmt
    elapsed_fmt=$(printf '%02d:%02d' $((elapsed/60)) $((elapsed%60)))
    local line="[A${i}] g${UI_SLOT_GROUP[$i]:--} ${UI_SLOT_LABEL[$i]:-idle} | ${UI_SLOT_PHASE[$i]:-idle} | ${UI_SLOT_MSG[$i]:-waiting} | ${elapsed_fmt} | ${UI_SLOT_LEVEL[$i]:-info}"
    line="$(_ui_sanitize_text "$line" 220)"
    printf '\r\033[2K%s\n' "$line"
  done
  UI_SLOTS_RENDERED=1
  UI_SLOTS_LAST_RENDER_SEC="$now"
}

ui_slots_flush() {
  if [[ "$UI_SLOTS_ENABLED" -eq 1 && "$UI_SLOTS_RENDERED" -eq 1 ]]; then
    printf '\033[%sA' "$UI_SLOTS_COUNT"
    local i
    for ((i=1; i<=UI_SLOTS_COUNT; i++)); do
      printf '\r\033[2K\n'
    done
    printf '\033[%sA' "$UI_SLOTS_COUNT"
    UI_SLOTS_RENDERED=0
  fi
}

ui_slots_stop() {
  if [[ "$UI_SLOTS_ENABLED" -eq 1 ]]; then
    ui_slots_render force
  fi
  UI_SLOTS_ENABLED=0
  UI_SLOTS_COUNT=0
  UI_SLOTS_RENDERED=0
}

_ui_prefix() {
  local stage="$1"
  shift || true
  ui_slots_flush
  ui_live_flush_line
  echo "[$stage] $*"
}

ui_print_run_header() {
  local workspace="$1"
  local run_id="$2"
  local base_branch="$3"
  local mode="$4"
  local model="$5"
  local sandbox="$6"

  _ui_prefix "Plan" "Workspace: $workspace"
  _ui_prefix "Plan" "Run ID: $run_id"
  _ui_prefix "Plan" "Base branch: $base_branch"
  _ui_prefix "Plan" "Execution mode: $mode"
  _ui_prefix "Plan" "Model: $model | Sandbox: $sandbox"
  local status_dir
  status_dir="$(ralphex_state_dir "$workspace")/parallel/$run_id"
  ui_emit_standard_event "$status_dir" "$run_id" "" "" "plan" "RUN_PLAN_READY" "info" "run plan header rendered" "$(jq -nc --arg base_branch "$base_branch" --arg mode "$mode" --arg model "$model" --arg sandbox "$sandbox" '{base_branch:$base_branch,mode:$mode,model:$model,sandbox:$sandbox}')" || true
}

ui_run_doctor_or_exit() {
  local workspace="$1"
  local had_errors=0
  local had_warnings=0
  local run_id_hint="${RALPHEX_RUN_ID_HINT:-}"
  local status_dir_hint=""
  if [[ -n "$run_id_hint" ]]; then
    status_dir_hint="$(ralphex_state_dir "$workspace")/parallel/$run_id_hint"
    ui_emit_standard_event "$status_dir_hint" "$run_id_hint" "" "" "doctor" "DOCTOR_STARTED" "info" "doctor started" || true
  fi

  _ui_prefix "Doctor" "Running preflight checks..."

  if git -C "$workspace" rev-parse --git-dir >/dev/null 2>&1; then
    local base
    if git -C "$workspace" show-ref --verify --quiet refs/heads/main; then
      base="main"
    elif git -C "$workspace" show-ref --verify --quiet refs/heads/master; then
      base="master"
    else
      base="$(git -C "$workspace" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    fi
    _ui_prefix "Doctor" "OK: git repo detected (base=$base)"
  else
    _ui_prefix "Doctor" "FAIL: git repository not found"
    had_errors=1
  fi

  if [[ -n "$(git -C "$workspace" ls-files -u 2>/dev/null)" ]]; then
    _ui_prefix "Doctor" "FAIL: unresolved merge conflicts present"
    had_errors=1
  else
    _ui_prefix "Doctor" "OK: no merge conflicts"
  fi

  if command -v codex >/dev/null 2>&1; then
    _ui_prefix "Doctor" "OK: codex available"
  else
    _ui_prefix "Doctor" "FAIL: codex missing in PATH"
    had_errors=1
  fi

  if command -v jq >/dev/null 2>&1; then
    _ui_prefix "Doctor" "OK: jq available"
  else
    _ui_prefix "Doctor" "FAIL: jq missing in PATH"
    had_errors=1
  fi

  if [[ -f "$workspace/AGENTS.md" ]]; then
    _ui_prefix "Doctor" "OK: AGENTS.md present"
  else
    _ui_prefix "Doctor" "WARN: AGENTS.md missing"
    had_warnings=1
  fi

  local task_file
  if [[ -f "$workspace/RALPHEX_TASK.md" ]]; then
    task_file="RALPHEX_TASK.md"
  elif [[ -f "$workspace/RALPH_TASK.md" ]]; then
    task_file="RALPH_TASK.md"
  else
    task_file=""
  fi
  if [[ -n "$task_file" ]]; then
    _ui_prefix "Doctor" "OK: $task_file present"
  else
    _ui_prefix "Doctor" "FAIL: task file missing (RALPHEX_TASK.md/RALPH_TASK.md)"
    had_errors=1
  fi

  if ! git -C "$workspace" diff --quiet || ! git -C "$workspace" diff --cached --quiet; then
    _ui_prefix "Doctor" "WARN: tracked changes present (parallel launch may be refused)"
    had_warnings=1
  else
    _ui_prefix "Doctor" "OK: tracked tree clean"
  fi

  _ui_prefix "Doctor" "Model preflight will run next."

  if [[ "$had_errors" -eq 1 ]]; then
    [[ -n "$run_id_hint" ]] && ui_emit_standard_event "$status_dir_hint" "$run_id_hint" "" "" "doctor" "DOCTOR_RESULT" "error" "doctor failed" || true
    _ui_prefix "Doctor" "Result: FAIL (blocking)"
    return 2
  fi
  if [[ "$had_warnings" -eq 1 ]]; then
    [[ -n "$run_id_hint" ]] && ui_emit_standard_event "$status_dir_hint" "$run_id_hint" "" "" "doctor" "DOCTOR_RESULT" "warn" "doctor warnings" || true
    _ui_prefix "Doctor" "Result: WARN (continuing)"
    return 0
  fi
  [[ -n "$run_id_hint" ]] && ui_emit_standard_event "$status_dir_hint" "$run_id_hint" "" "" "doctor" "DOCTOR_RESULT" "info" "doctor ok" || true
  _ui_prefix "Doctor" "Result: OK"
  return 0
}

ui_print_task_inventory() {
  local workspace="$1"
  local json
  json=$(get_inventory_counts "$workspace")
  local total completed pending groups_total groups_pending
  total=$(echo "$json" | jq -r '.total_tasks')
  completed=$(echo "$json" | jq -r '.completed_tasks')
  pending=$(echo "$json" | jq -r '.pending_tasks')
  groups_total=$(echo "$json" | jq -r '.total_groups')
  groups_pending=$(echo "$json" | jq -r '.pending_groups')

  _ui_prefix "Plan" "Tasks: total=$total completed=$completed pending=$pending"
  _ui_prefix "Plan" "Groups: total=$groups_total pending=$groups_pending completed=$((groups_total-groups_pending))"
}

ui_print_group_plan() {
  local workspace="$1"
  local group="$2"
  local blocked="${3:-0}"
  local counts_json
  counts_json=$(get_group_counts "$workspace" "$group")
  local total completed pending
  total=$(echo "$counts_json" | jq -r '.total')
  completed=$(echo "$counts_json" | jq -r '.completed')
  pending=$(echo "$counts_json" | jq -r '.pending')

  _ui_prefix "Group $group" "Plan: total=$total | pending=$pending | already-completed/skipped=$completed | blocked=$blocked"
  echo "$counts_json"
}

ui_detect_group_mode() {
  local workspace="$1"
  local group="$2"
  local max_parallel="$3"
  local pending
  pending=$(get_tasks_by_group "$workspace" "$group" || true)
  if [[ "$max_parallel" -le 1 ]]; then
    echo "sequential"
    return 0
  fi
  local seq_count non_seq_count
  seq_count=$(echo "$pending" | awk -F'|' '$8=="true"' | sed '/^$/d' | wc -l | tr -d ' ')
  non_seq_count=$(echo "$pending" | awk -F'|' '$8!="true"' | sed '/^$/d' | wc -l | tr -d ' ')
  if [[ "$seq_count" -gt 0 && "$non_seq_count" -gt 0 ]]; then
    echo "mixed"
  elif [[ "$seq_count" -gt 0 ]]; then
    echo "sequential"
  else
    echo "parallel"
  fi
}

ui_print_group_start() {
  local group="$1"
  local mode="$2"
  local counts_json="$3"
  local pending completed
  pending=$(echo "$counts_json" | jq -r '.pending')
  completed=$(echo "$counts_json" | jq -r '.completed')
  ui_live_update "g${group}" "starting group in $mode mode (pending=$pending skipped=$completed)"
  _ui_prefix "Group $group" "Starting group in $mode mode (pending=$pending, skipped=$completed)"
}

ui_print_group_task_result() {
  local task_id="$1"
  local result="$2"
  local reason="${3:-}"
  ui_live_update "task $task_id" "$result${reason:+ ($reason)}"
  if [[ -n "$reason" ]]; then
    _ui_prefix "Task $task_id" "$result ($reason)"
  else
    _ui_prefix "Task $task_id" "$result"
  fi
}

ui_print_orchestrator_start() {
  local group="$1"
  ui_live_update "g${group}" "orchestrator stage entered"
  _ui_prefix "Orchestrator g$group" "Entering orchestrator stage (merge -> checkpoint -> cleanup)"
}

ui_print_orchestrator_step() {
  local group="$1"
  local step="$2"
  local detail="${3:-}"
  ui_live_update "g${group}" "orchestrator $step${detail:+: $detail}"
  _ui_prefix "Orchestrator g$group" "$step${detail:+: $detail}"
}

ui_print_orchestrator_done() {
  local group="$1"
  local status="$2"
  local commit_sha="${3:-}"
  ui_live_update "g${group}" "orchestrator done: $status${commit_sha:+ (commit=$commit_sha)}"
  if [[ -n "$commit_sha" ]]; then
    _ui_prefix "Orchestrator g$group" "Done: $status (commit=$commit_sha)"
  else
    _ui_prefix "Orchestrator g$group" "Done: $status"
  fi
}

ui_print_group_done() {
  local group="$1"
  local stats_json="$2"
  local merged failed blocked
  merged=$(echo "$stats_json" | jq -r '.merged // 0')
  failed=$(echo "$stats_json" | jq -r '.failed // 0')
  blocked=$(echo "$stats_json" | jq -r '.blocked // 0')
  ui_live_update "g${group}" "group completed (merged=$merged failed=$failed blocked=$blocked)"
  _ui_prefix "Group $group" "Completed (merged=$merged failed=$failed blocked=$blocked)"
}

ui_print_final_summary() {
  local run_id="$1"
  local aggregate_json="$2"
  _ui_prefix "Summary" "Run $run_id complete summary:"
  _ui_prefix "Summary" "Groups: completed=$(echo "$aggregate_json" | jq -r '.groups.completed') failed=$(echo "$aggregate_json" | jq -r '.groups.failed') skipped=$(echo "$aggregate_json" | jq -r '.groups.skipped')"
  _ui_prefix "Summary" "Tasks: executed=$(echo "$aggregate_json" | jq -r '.tasks.executed') merged=$(echo "$aggregate_json" | jq -r '.tasks.merged') skipped=$(echo "$aggregate_json" | jq -r '.tasks.skipped') blocked=$(echo "$aggregate_json" | jq -r '.tasks.blocked') failed=$(echo "$aggregate_json" | jq -r '.tasks.failed')"
  _ui_prefix "Summary" "Orchestrator: attempted=$(echo "$aggregate_json" | jq -r '.orchestrator.attempted') succeeded=$(echo "$aggregate_json" | jq -r '.orchestrator.succeeded') failed=$(echo "$aggregate_json" | jq -r '.orchestrator.failed') cleanup_ok=$(echo "$aggregate_json" | jq -r '.orchestrator.cleanup_ok')"
  _ui_prefix "Summary" "Head: $(echo "$aggregate_json" | jq -r '.head.sha') on $(echo "$aggregate_json" | jq -r '.head.branch') | main_clean=$(echo "$aggregate_json" | jq -r '.head.main_clean')"
  local next_action
  next_action=$(echo "$aggregate_json" | jq -r '.next_action')
  [[ -n "$next_action" && "$next_action" != "null" ]] && _ui_prefix "Summary" "Next: $next_action"
}
