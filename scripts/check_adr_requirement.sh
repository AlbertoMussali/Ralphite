#!/usr/bin/env bash
set -euo pipefail

# Enforce ADR updates on PRs when architecture/runtime contract surfaces change.
# No-op for non-PR runs.

if [[ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]]; then
  echo "ADR requirement check skipped: not a pull_request event."
  exit 0
fi

if [[ -z "${GITHUB_BASE_REF:-}" ]]; then
  echo "ADR requirement check skipped: GITHUB_BASE_REF not set."
  exit 0
fi

BASE_REF="origin/${GITHUB_BASE_REF}"
git fetch origin "${GITHUB_BASE_REF}" --depth=1

CHANGED="$(git diff --name-only "${BASE_REF}"...HEAD)"
if [[ -z "${CHANGED}" ]]; then
  echo "No changed files detected."
  exit 0
fi

if ! echo "${CHANGED}" | rg -q \
  "^(packages/engine/src/ralphite_engine/(headless_agent|orchestrator)\.py|apps/cli/src/ralphite_cli/.*\.py|packages/schemas/json/plan-spec\.schema\.json|packages/schemas/python/src/ralphite_schemas/plan\.py)$"; then
  echo "ADR requirement check passed: no contract-critical files changed."
  exit 0
fi

if echo "${CHANGED}" | rg -q "^docs/decisions/ADR-.*\\.md$"; then
  echo "ADR requirement check passed: ADR file updated."
  exit 0
fi

echo "ADR requirement check failed."
echo "Contract-critical files changed but no ADR update was found under docs/decisions/ADR-*.md."
echo "Changed files:"
echo "${CHANGED}"
exit 1
