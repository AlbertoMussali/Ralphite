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
        emit_if_match "$text"
      fi

      if [[ "$itype" == "command_execution" ]]; then
        local cmd exit_code status
        cmd=$(echo "$line" | jq -r '.item.command // empty')
        exit_code=$(echo "$line" | jq -r '.item.exit_code // -1')
        status=$(echo "$line" | jq -r '.item.status // empty')
        log_activity "CMD done: status=$status exit=$exit_code cmd=$cmd"
        if [[ "$exit_code" != "0" ]]; then
          echo "$cmd" >> "$FAILURES_FILE"
          local count
          count=$(grep -Fxc "$cmd" "$FAILURES_FILE" || true)
          log_error "CMD failed (attempt $count): exit=$exit_code cmd=$cmd"
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
