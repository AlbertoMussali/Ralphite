#!/bin/bash
# Initialize Ralphex in a project

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET="${1:-.}"
TARGET="$(cd "$TARGET" && pwd)"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is required." >&2
  exit 1
fi

mkdir -p "$TARGET/.cursor/ralphex-scripts"
mkdir -p "$TARGET/.ralphex"

if [[ ! -f "$TARGET/RALPHEX_TASK.md" ]]; then
  cat > "$TARGET/RALPHEX_TASK.md" <<'EOT'
---
task: Describe your task
test_command: "echo no tests configured"
---

# Task

Describe what to build.

## Success Criteria

1. [ ] First criterion
2. [ ] Second criterion
EOT
fi

for f in "$SCRIPT_DIR"/*.sh; do
  cp "$f" "$TARGET/.cursor/ralphex-scripts/$(basename "$f")"
  chmod +x "$TARGET/.cursor/ralphex-scripts/$(basename "$f")"
done

[[ -f "$TARGET/.ralphex/.iteration" ]] || echo "0" > "$TARGET/.ralphex/.iteration"
[[ -f "$TARGET/.ralphex/session_id" ]] || : > "$TARGET/.ralphex/session_id"
[[ -f "$TARGET/.ralphex/activity.log" ]] || echo "# Activity Log" > "$TARGET/.ralphex/activity.log"
[[ -f "$TARGET/.ralphex/errors.log" ]] || echo "# Error Log" > "$TARGET/.ralphex/errors.log"
[[ -f "$TARGET/.ralphex/progress.md" ]] || echo "# Progress Log" > "$TARGET/.ralphex/progress.md"
[[ -f "$TARGET/.ralphex/guardrails.md" ]] || echo "# Ralphex Guardrails" > "$TARGET/.ralphex/guardrails.md"

if [[ -f "$TARGET/.gitignore" ]]; then
  if ! grep -q '^\.cursor/ralphex-config\.json$' "$TARGET/.gitignore"; then
    {
      echo ""
      echo "# Ralphex config"
      echo ".cursor/ralphex-config.json"
    } >> "$TARGET/.gitignore"
  fi
else
  echo ".cursor/ralphex-config.json" > "$TARGET/.gitignore"
fi

echo "Ralphex initialized at $TARGET"
echo "Run: $TARGET/.cursor/ralphex-scripts/ralphex-loop.sh"
