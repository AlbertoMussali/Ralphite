#!/bin/bash
# Ralphex stream parser for `codex exec --json`

set -euo pipefail

WORKSPACE="${1:-.}"
STATE_DIR="$WORKSPACE/.ralphex"
SESSION_FILE="$STATE_DIR/session_id"

mkdir -p "$STATE_DIR"
touch "$SESSION_FILE" "$STATE_DIR/activity.log" "$STATE_DIR/errors.log"

WARN_THRESHOLD="${RALPHEX_WARN_TOKENS:-380000}"
ROTATE_THRESHOLD="${RALPHEX_ROTATE_TOKENS:-400000}"
WARN_SENT=0
HEARTBEAT_COALESCE_SECONDS="${RALPHEX_HEARTBEAT_COALESCE_SECONDS:-1}"
LAST_HEARTBEAT_EPOCH=0

STATUS_DIR="${RALPHEX_STREAM_STATUS_DIR:-}"
RUN_ID="${RALPHEX_STREAM_RUN_ID:-}"
GROUP_ID="${RALPHEX_STREAM_GROUP:-}"
TASK_ID="${RALPHEX_STREAM_TASK_ID:-}"
STAGE="${RALPHEX_STREAM_STAGE:-task}"
PROGRESS_FILE=""
if [[ -n "$STATUS_DIR" ]]; then
  mkdir -p "$STATUS_DIR" >/dev/null 2>&1 || true
  PROGRESS_FILE="$STATUS_DIR/progress.jsonl"
fi

FAILURES_FILE=$(mktemp)
trap 'rm -f "$FAILURES_FILE"' EXIT

log_activity() {
  local msg="$1"
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$msg" >> "$STATE_DIR/activity.log"
}

log_error() {
  local msg="$1"
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$msg" >> "$STATE_DIR/errors.log"
}

sanitize_text() {
  local input="${1:-}"
  local max_len="${2:-120}"
  local out
  out=$(printf '%s' "$input" | tr '\r\n' ' ' | tr -cd '\11\12\15\40-\176')
  out=$(printf '%s' "$out" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
  if [[ ${#out} -gt "$max_len" ]]; then
    out="${out:0:$((max_len-3))}..."
  fi
  printf '%s' "$out"
}

emit_progress_event() {
  local event="$1"
  local level="$2"
  local message="$3"
  local meta="${4:-{}}"
  [[ -n "$PROGRESS_FILE" && -n "$RUN_ID" ]] || return 0
  if ! jq -e . >/dev/null 2>&1 <<<"$meta"; then
    meta=$(jq -nc --arg raw "$meta" '{detail_raw:$raw}')
  fi
  jq -nc \
    --arg ts "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --arg run_id "$RUN_ID" \
    --arg group "$GROUP_ID" \
    --arg task_id "$TASK_ID" \
    --arg stage "$STAGE" \
    --arg event "$event" \
    --arg level "$level" \
    --arg message "$(sanitize_text "$message" 300)" \
    --argjson meta "$meta" \
    '{ts:$ts,run_id:$run_id,group:($group|if .=="" then null else . end),task_id:($task_id|if .=="" then null else . end),stage:$stage,event:$event,level:$level,message:$message,meta:$meta}' >>"$PROGRESS_FILE" 2>/dev/null || true
}

emit_heartbeat() {
  local summary="$1"
  local meta="${2:-{}}"
  local now
  now=$(date +%s)
  if [[ $((now - LAST_HEARTBEAT_EPOCH)) -lt "$HEARTBEAT_COALESCE_SECONDS" ]]; then
    return 0
  fi
  LAST_HEARTBEAT_EPOCH=$now
  emit_progress_event "TASK_HEARTBEAT" "info" "$(sanitize_text "$summary" 160)" "$meta"
}

extract_phase() {
  local text
  text=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  if [[ "$text" == *"resolv"* && "$text" == *"conflict"* ]]; then
    echo "resolving merge conflicts"; return
  fi
  if [[ "$text" == *"test"* ]]; then
    echo "running tests"; return
  fi
  if [[ "$text" == *"build"* ]]; then
    echo "building"; return
  fi
  if [[ "$text" == *"read"* || "$text" == *"inspect"* ]]; then
    echo "inspecting files"; return
  fi
  if [[ "$text" == *"patch"* || "$text" == *"apply"* || "$text" == *"edit"* ]]; then
    echo "applying code changes"; return
  fi
  echo "working"
}

emit_if_match() {
  local text="$1"
  if [[ "$text" == *"<ralphex>COMPLETE</ralphex>"* || "$text" == *"<ralph>COMPLETE</ralph>"* ]]; then
    echo "COMPLETE"
  fi
  if [[ "$text" == *"<ralphex>GUTTER</ralphex>"* || "$text" == *"<ralph>GUTTER</ralph>"* ]]; then
    echo "GUTTER"
  fi
}

check_token_thresholds() {
  local total="$1"
  if [[ "$total" -ge "$ROTATE_THRESHOLD" ]]; then
    log_activity "ROTATE threshold reached: $total/$ROTATE_THRESHOLD"
    echo "ROTATE"
    return
  fi
  if [[ "$total" -ge "$WARN_THRESHOLD" ]] && [[ "$WARN_SENT" -eq 0 ]]; then
    WARN_SENT=1
    log_activity "WARN threshold reached: $total/$ROTATE_THRESHOLD"
    echo "WARN"
  fi
}

handle_json() {
  local line="$1"
  local type

  type=$(echo "$line" | jq -r '.type // empty' 2>/dev/null || true)
  [[ -z "$type" ]] && return 0

  case "$type" in
    thread.started)
      local tid
      tid=$(echo "$line" | jq -r '.thread_id // empty')
      if [[ -n "$tid" ]]; then
        echo "$tid" > "$SESSION_FILE"
        log_activity "THREAD started: $tid"
      fi
      ;;
    turn.started)
      log_activity "TURN started"
      ;;
    item.started)
      local itype
      itype=$(echo "$line" | jq -r '.item.type // empty')
      if [[ "$itype" == "command_execution" ]]; then
        local cmd
        cmd=$(echo "$line" | jq -r '.item.command // empty')
        log_activity "CMD started: $cmd"
      fi
      ;;
    item.completed)
      local itype
      itype=$(echo "$line" | jq -r '.item.type // empty')

      if [[ "$itype" == "agent_message" ]]; then
        local text
        text=$(echo "$line" | jq -r '.item.text // empty')
        log_activity "AGENT: ${text:0:160}"
        if [[ "${RALPHEX_REASONING_SUMMARY:-1}" == "1" && -n "$text" ]]; then
          emit_heartbeat "agent: $(extract_phase "$text")" "$(jq -nc --arg phase "$(extract_phase "$text")" '{agent_phase:$phase}')"
        fi
        emit_if_match "$text"
      fi

      if [[ "$itype" == "reasoning" ]]; then
        local text
        text=$(echo "$line" | jq -r '.item.text // empty')
        log_activity "REASON: ${text:0:160}"
        if [[ "${RALPHEX_REASONING_SUMMARY:-1}" == "1" && -n "$text" ]]; then
          emit_heartbeat "agent: $(extract_phase "$text")" "$(jq -nc --arg phase "$(extract_phase "$text")" '{agent_phase:$phase}')"
        fi
      fi

      if [[ "$itype" == "command_execution" ]]; then
        local cmd exit_code status
        cmd=$(echo "$line" | jq -r '.item.command // empty')
        exit_code=$(echo "$line" | jq -r '.item.exit_code // -1')
        status=$(echo "$line" | jq -r '.item.status // empty')
        log_activity "CMD done: status=$status exit=$exit_code cmd=$cmd"
        local cmd_base
        cmd_base=$(echo "$cmd" | sed -E 's/^.* -lc '\''?//; s/'\''?$//' | awk '{print $1}')
        [[ -z "$cmd_base" ]] && cmd_base="command"
        emit_heartbeat "agent: ran $cmd_base (exit=$exit_code)" "$(jq -nc --arg last_cmd "$cmd_base" --arg exit_code "$exit_code" '{last_cmd:$last_cmd,exit_code:($exit_code|tonumber)}')"
        if [[ "$exit_code" != "0" ]]; then
          echo "$cmd" >> "$FAILURES_FILE"
          local count
          count=$(grep -Fxc "$cmd" "$FAILURES_FILE" || true)
          log_error "CMD failed (attempt $count): exit=$exit_code cmd=$cmd"
          emit_progress_event "TASK_HEARTBEAT" "warn" "agent command failed (exit=$exit_code)" "$(jq -nc --arg last_cmd "$cmd_base" --arg exit_code "$exit_code" '{last_cmd:$last_cmd,exit_code:($exit_code|tonumber)}')"
          if [[ "$count" -ge 3 ]]; then
            log_error "GUTTER: same command failed 3+ times"
            echo "GUTTER"
          fi
        fi
      fi
      ;;
    turn.completed)
      local in out cached total
      in=$(echo "$line" | jq -r '.usage.input_tokens // 0')
      out=$(echo "$line" | jq -r '.usage.output_tokens // 0')
      cached=$(echo "$line" | jq -r '.usage.cached_input_tokens // 0')
      total=$((in + out))
      log_activity "TOKENS input=$in cached=$cached output=$out total=$total"
      check_token_thresholds "$total"
      ;;
  esac
}

main() {
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue

    # Codex may emit non-JSON warnings on stderr; ignore those for parsing.
    if [[ "$line" == \{*\} ]]; then
      handle_json "$line"
    else
      emit_if_match "$line"
    fi
  done
}

main
