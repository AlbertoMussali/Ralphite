#!/bin/bash
# Ralphex task parser (portable bash implementation)

set -euo pipefail

_meta_extract() {
  local meta="$1"
  local key="$2" # tools|test|seq|deps|group
  awk -v k="$key" '
    function ltrim(s) { sub(/^[[:space:]]+/, "", s); return s }
    function rtrim(s) { sub(/[[:space:]]+$/, "", s); return s }
    function trim(s) { return rtrim(ltrim(s)) }
    function extract(key, str,    p, rest) {
      p = index(str, key)
      if (p == 0) return ""
      rest = substr(str, p + length(key))
      rest = trim(rest)
      match(rest, /[[:space:]][A-Za-z_]+:[[:space:]]/)
      if (RSTART > 0) return trim(substr(rest, 1, RSTART - 1))
      return trim(rest)
    }
    {
      # Normalize: ensure key ends with colon
      key = k ":"
      print extract(key, $0)
      exit
    }
  ' <<<"$meta"
}

_parse_tasks_stream() {
  local workspace="${1:-.}"
  local task_file=""
  local line_no=0

  if [[ -f "$workspace/RALPHEX_TASK.md" ]]; then
    task_file="$workspace/RALPHEX_TASK.md"
  elif [[ -f "$workspace/RALPH_TASK.md" ]]; then
    task_file="$workspace/RALPH_TASK.md"
  else
    return 1
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    line_no=$((line_no + 1))

    if [[ "$line" =~ ^[[:space:]]*([-*]|[0-9]+\.)[[:space:]]+\[([xX[:space:]])\][[:space:]]+(.*)$ ]]; then
      local status_char="${BASH_REMATCH[2]}"
      local desc="${BASH_REMATCH[3]}"
      local status="pending"
      local group="999999"
      local id="line_${line_no}"
      local meta=""
      local tools=""
      local test_cmd=""
      local seq="false"
      local deps=""

      if [[ "$status_char" == "x" || "$status_char" == "X" ]]; then
        status="completed"
      fi

      meta=$(printf '%s\n' "$line" | sed -nE 's/.*<!--[[:space:]]*(.*)[[:space:]]*-->[[:space:]]*$/\1/p' || true)
      if [[ -n "$meta" ]]; then
        local g
        g=$(_meta_extract "$meta" "group" || true)
        [[ -n "$g" ]] && group="$g"

        tools=$(_meta_extract "$meta" "tools" || true)
        test_cmd=$(_meta_extract "$meta" "test" || true)
        deps=$(_meta_extract "$meta" "deps" || true)

        local s
        s=$(_meta_extract "$meta" "seq" || true)
        if [[ "$s" =~ ^(true|1|yes)$ ]]; then
          seq="true"
        fi
      fi

      # Strip trailing metadata comment from description (if present).
      desc=$(echo "$desc" | sed -E 's/[[:space:]]*<!--[[:space:]]*.*-->[[:space:]]*$//')

      printf '%s|%s|%s|%s|%s|%s|%s|%s|%s\n' "$id" "$status" "$group" "$desc" "$line_no" "$tools" "$test_cmd" "$seq" "$deps"
    fi
  done < "$task_file"
}

parse_tasks() {
  local workspace="${1:-.}"
  [[ -f "$workspace/RALPHEX_TASK.md" || -f "$workspace/RALPH_TASK.md" ]]
}

get_all_tasks() {
  local workspace="${1:-.}"
  _parse_tasks_stream "$workspace" | awk -F'|' '{print $1 "|" $2 "|" $4}'
}

get_all_tasks_with_group() {
  local workspace="${1:-.}"
  _parse_tasks_stream "$workspace"
}

get_pending_groups() {
  local workspace="${1:-.}"
  _parse_tasks_stream "$workspace" | awk -F'|' '$2=="pending" {print $3}' | sort -n | uniq
}

get_tasks_by_group() {
  local workspace="${1:-.}"
  local group="$2"
  _parse_tasks_stream "$workspace" | awk -F'|' -v g="$group" '$2=="pending" && $3==g {print}'
}

get_next_task() {
  local workspace="${1:-.}"
  # NOTE: Under `set -o pipefail`, the producer (`_parse_tasks_stream`) can exit
  # with SIGPIPE when `awk` exits early. That is expected and should not be
  # treated as an error for "get first match" helpers.
  _parse_tasks_stream "$workspace" | awk -F'|' '$2=="pending" {print $1 "|" $4 "|" $5; exit}' || true
}

get_next_tasks() {
  local workspace="${1:-.}"
  local n="${2:-3}"
  # Same SIGPIPE caveat as get_next_task().
  _parse_tasks_stream "$workspace" | awk -F'|' -v n="$n" '$2=="pending" {print; c++; if (c>=n) exit}' || true
}

get_task_by_id() {
  local workspace="${1:-.}"
  local task_id="$2"
  _parse_tasks_stream "$workspace" | awk -F'|' -v id="$task_id" '$1==id {print; exit}'
}

mark_task_complete() {
  local workspace="${1:-.}"
  local task_id="$2"
  local task_file=""
  local line_no

  if [[ -f "$workspace/RALPHEX_TASK.md" ]]; then
    task_file="$workspace/RALPHEX_TASK.md"
  else
    task_file="$workspace/RALPH_TASK.md"
  fi

  line_no=$(echo "$task_id" | sed -E 's/^line_([0-9]+)$/\1/')
  [[ "$line_no" =~ ^[0-9]+$ ]] || { echo "invalid task id: $task_id" >&2; return 1; }

  if [[ "$OSTYPE" == darwin* ]]; then
    sed -i '' "${line_no}s/\[ \]/[x]/;${line_no}s/\[X\]/[x]/" "$task_file"
  else
    sed -i "${line_no}s/\[ \]/[x]/;${line_no}s/\[X\]/[x]/" "$task_file"
  fi
}

mark_task_incomplete() {
  local workspace="${1:-.}"
  local task_id="$2"
  local task_file=""
  local line_no

  if [[ -f "$workspace/RALPHEX_TASK.md" ]]; then
    task_file="$workspace/RALPHEX_TASK.md"
  else
    task_file="$workspace/RALPH_TASK.md"
  fi

  line_no=$(echo "$task_id" | sed -E 's/^line_([0-9]+)$/\1/')
  [[ "$line_no" =~ ^[0-9]+$ ]] || { echo "invalid task id: $task_id" >&2; return 1; }

  if [[ "$OSTYPE" == darwin* ]]; then
    sed -i '' "${line_no}s/\[[xX]\]/[ ]/" "$task_file"
  else
    sed -i "${line_no}s/\[[xX]\]/[ ]/" "$task_file"
  fi
}

get_progress() {
  local workspace="${1:-.}"
  local total done

  total=$(_parse_tasks_stream "$workspace" | wc -l | tr -d ' ')
  done=$(_parse_tasks_stream "$workspace" | awk -F'|' '$2=="completed"' | wc -l | tr -d ' ')
  echo "$done|$total"
}
