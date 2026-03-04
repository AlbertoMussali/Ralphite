#!/bin/bash
# Ralphex interactive setup

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ralphex-common.sh"
source "$SCRIPT_DIR/ralphex-parallel.sh"

HAS_GUM=false
command -v gum >/dev/null 2>&1 && HAS_GUM=true

workspace="${1:-.}"
workspace="$(cd "$workspace" && pwd)"

check_prerequisites "$workspace"
init_ralph_dir "$workspace"

if [[ "$HAS_GUM" == true ]]; then
  MODEL=$(gum input --header "Model" --value "$MODEL")
  SANDBOX=$(gum choose workspace-write danger-full-access read-only)
  MAX_ITERATIONS=$(gum input --header "Max iterations" --value "$MAX_ITERATIONS")
  mode=$(gum choose "Sequential" "Parallel")
else
  echo "Model [$MODEL]:"
  read -r in_model
  MODEL="${in_model:-$MODEL}"

  echo "Sandbox [$SANDBOX] (workspace-write|danger-full-access|read-only):"
  read -r in_sb
  SANDBOX="${in_sb:-$SANDBOX}"

  echo "Max iterations [$MAX_ITERATIONS]:"
  read -r in_it
  MAX_ITERATIONS="${in_it:-$MAX_ITERATIONS}"

  echo "Mode [Sequential/Parallel] (default Sequential):"
  read -r mode
  mode="${mode:-Sequential}"
fi

show_banner
show_task_summary "$workspace"

if [[ "$mode" == "Parallel" ]]; then
  if [[ "$HAS_GUM" == true ]]; then
    max_parallel=$(gum input --header "Max parallel agents" --value "3")
  else
    echo "Max parallel agents [3]:"
    read -r max_parallel
    max_parallel="${max_parallel:-3}"
  fi
  run_parallel_tasks "$workspace" "$max_parallel" ""
else
  run_ralphex_loop "$workspace" "$SCRIPT_DIR"
fi
