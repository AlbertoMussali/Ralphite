#!/bin/bash
# Ralphex one-iteration runner

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-common.sh"

WORKSPACE=""

show_help() {
  cat <<'EOT'
Ralphex Once

Usage:
  ./ralphex-once.sh [options] [workspace]

Options:
  -m, --model MODEL
  -s, --sandbox MODE
  -h, --help
EOT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--model)
      MODEL="$2"; shift 2 ;;
    -s|--sandbox)
      SANDBOX="$2"; shift 2 ;;
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

check_prerequisites "$WORKSPACE"
init_ralph_dir "$WORKSPACE"
show_task_summary "$WORKSPACE"

echo "Running single Ralphex iteration..."
result=$(run_iteration "$WORKSPACE" "$SCRIPT_DIR")
echo "Result: $result"

if [[ "$result" == "GUTTER" ]]; then
  exit 2
fi

exit 0
