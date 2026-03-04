#!/bin/bash
# Ralphex retry utilities

set -euo pipefail

DEFAULT_MAX_RETRIES="${DEFAULT_MAX_RETRIES:-3}"
DEFAULT_BASE_DELAY="${DEFAULT_BASE_DELAY:-2}"
DEFAULT_MAX_DELAY="${DEFAULT_MAX_DELAY:-30}"

is_retryable_error() {
  local msg="${1:-}"
  local lower
  lower=$(echo "$msg" | tr '[:upper:]' '[:lower:]')

  [[ "$lower" =~ (rate[[:space:]_-]*limit|429|too[[:space:]]*many[[:space:]]*requests) ]] && return 0
  [[ "$lower" =~ (timeout|timed[[:space:]]*out|connection[[:space:]]*(reset|refused|closed|failed)|network[[:space:]]*error) ]] && return 0
  [[ "$lower" =~ (service[[:space:]]*unavailable|503|bad[[:space:]]*gateway|502|gateway[[:space:]]*timeout|504|overloaded) ]] && return 0

  return 1
}

_backoff_seconds() {
  local attempt="$1"
  local base="$2"
  local max_delay="$3"
  local delay=$((base << (attempt - 1)))
  if [[ "$delay" -gt "$max_delay" ]]; then
    delay="$max_delay"
  fi
  echo "$delay"
}

with_retry() {
  local max_retries="$1"
  local base_delay="$2"
  shift 2

  local attempt=1
  local last_rc=0
  local output=""

  while [[ "$attempt" -le "$max_retries" ]]; do
    set +e
    output=$("$@" 2>&1)
    last_rc=$?
    set -e

    if [[ "$last_rc" -eq 0 ]]; then
      echo "$output"
      return 0
    fi

    if [[ "$attempt" -ge "$max_retries" ]]; then
      echo "$output" >&2
      return "$last_rc"
    fi

    if ! is_retryable_error "$output"; then
      echo "$output" >&2
      return "$last_rc"
    fi

    local sleep_for
    sleep_for=$(_backoff_seconds "$attempt" "$base_delay" "$DEFAULT_MAX_DELAY")
    echo "Retryable failure (attempt $attempt/$max_retries). Retrying in ${sleep_for}s..." >&2
    sleep "$sleep_for"
    attempt=$((attempt + 1))
  done

  echo "$output" >&2
  return "$last_rc"
}

retry() {
  with_retry "$DEFAULT_MAX_RETRIES" "$DEFAULT_BASE_DELAY" "$@"
}
