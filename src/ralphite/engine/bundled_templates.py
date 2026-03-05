from __future__ import annotations

# Inline agent/behavior block shared across all starter templates.
# Avoids relying on an external agent_defaults.yaml which makes templates
# fragile in fresh workspaces and unit tests.
_INLINE_AGENTS = """\
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
orchestration:
  inference_mode: mixed
  behaviors:
    - id: prepare_dispatch_default
      kind: prepare_dispatch
      agent: orchestrator_default
      enabled: true
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
    - id: summarize_default
      kind: summarize_work
      agent: orchestrator_default
      enabled: true\
"""

STARTER_BUGFIX = """\
# Starter template for bugfix work.
# Use this when you already have a reported failure and need a focused reproduce -> fix -> regression loop.
# Customize first: plan_id, name, task titles, and the acceptance commands/artifacts that prove this specific bug is fixed.
# Task titles and rubrics below are intentional placeholders for one concrete bugfix, not generic schema examples.
version: 1
plan_id: starter_bugfix
name: Starter Bugfix
materials:
  autodiscover:
    enabled: true
    path: .
    include_globs:
      - '**/*.yaml'
      - '**/*.yml'
      - '**/*.md'
      - '**/*.txt'
  includes: []
  uploads: []
constraints:
  max_runtime_seconds: 3600
  max_total_steps: 120
  max_cost_usd: 10.0
  fail_fast: true
  max_parallel: 3
  acceptance_timeout_seconds: 120
  max_retries_per_node: 0
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
tasks:
  - id: task_scope_bug
    title: Reproduce the reported bug, confirm impact, and define the safe fix boundary.
    completed: false
    routing:
      cell: cycle
      team_mode: blue_red
      tags: [bugfix, triage]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Reproduction steps, expected behavior, and observed failure are explicit.
        - Root-cause hypotheses and out-of-scope risks are captured before code changes begin.
  - id: task_apply_fix
    title: Implement the smallest credible fix and keep the change scoped to the reported failure.
    completed: false
    deps: [task_scope_bug]
    routing:
      cell: cycle
      team_mode: blue_red
      tags: [bugfix, implementation]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - The change resolves the confirmed failure without broadening scope into adjacent cleanup.
        - Replace this placeholder with the regression command or artifact that proves the fix.
  - id: task_regression_review
    title: Run regression checks, review edge cases, and summarize any remaining risk.
    completed: false
    deps: [task_apply_fix]
    routing:
      cell: cycle
      team_mode: blue_red
      tags: [bugfix, verification]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Replace this placeholder with the checks that guard against the bug returning.
        - Final notes distinguish what is now fixed from any follow-up work left intentionally open.
orchestration:
  template: blue_red
  inference_mode: mixed
  behaviors:
    - id: prepare_dispatch_default
      kind: prepare_dispatch
      agent: orchestrator_default
      enabled: true
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
    - id: summarize_default
      kind: summarize_work
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
outputs:
  required_artifacts:
    - id: final_report
      format: markdown
    - id: machine_bundle
      format: json
"""

STARTER_REFACTOR = """\
# Starter template for refactor work.
# Use this when behavior should stay stable while internals, boundaries, or structure get cleaner.
# Customize first: plan_id, name, the invariants that must not change, and the verification commands that prove parity.
# Task titles assume one bounded refactor slice with explicit before/after verification.
version: 1
plan_id: starter_refactor
name: Starter Refactor
materials:
  autodiscover:
    enabled: true
    path: .
    include_globs:
      - '**/*.yaml'
      - '**/*.yml'
      - '**/*.md'
      - '**/*.txt'
  includes: []
  uploads: []
constraints:
  max_runtime_seconds: 3600
  max_total_steps: 120
  max_cost_usd: 10.0
  fail_fast: true
  max_parallel: 3
  acceptance_timeout_seconds: 120
  max_retries_per_node: 0
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
tasks:
  - id: task_capture_invariants
    title: Capture current behavior, safety constraints, and the refactor boundary before moving code.
    completed: false
    routing:
      cell: seq_pre
      tags: [refactor, planning]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Existing behavior, public contracts, and no-regression invariants are explicit.
        - The refactor boundary is narrow enough to verify without bundling unrelated cleanup.
  - id: task_perform_refactor
    title: Restructure the targeted code paths without changing intended behavior.
    completed: false
    deps: [task_capture_invariants]
    routing:
      cell: par_core
      tags: [refactor, implementation]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Internal structure is meaningfully improved while the agreed behavior contract remains intact.
        - Replace this placeholder with the focused checks or artifacts that prove the refactor is safe.
  - id: task_verify_parity
    title: Verify behavior parity, remove dead edges, and summarize any follow-up debt.
    completed: false
    deps: [task_perform_refactor]
    routing:
      cell: seq_post
      tags: [refactor, verification]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Verification shows no intentional product behavior change beyond the documented refactor goal.
        - Final notes call out any debt deferred instead of silently expanding scope.
orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: prepare_dispatch_default
      kind: prepare_dispatch
      agent: orchestrator_default
      enabled: true
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
    - id: summarize_default
      kind: summarize_work
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
outputs:
  required_artifacts:
    - id: final_report
      format: markdown
    - id: machine_bundle
      format: json
"""

STARTER_DOCS_UPDATE = """\
# Starter template for documentation work.
# Use this when docs, examples, or operator guidance must be updated to match current behavior.
# Customize first: plan_id, name, the source-of-truth files to audit, and the commands that verify docs stayed consistent.
# Task titles assume one docs change set that starts from code/tests truth rather than inferred intent.
version: 1
plan_id: starter_docs_update
name: Starter Docs Update
materials:
  autodiscover:
    enabled: true
    path: .
    include_globs:
      - '**/*.yaml'
      - '**/*.yml'
      - '**/*.md'
      - '**/*.txt'
  includes: []
  uploads: []
constraints:
  max_runtime_seconds: 3600
  max_total_steps: 120
  max_cost_usd: 10.0
  fail_fast: true
  max_parallel: 3
  acceptance_timeout_seconds: 120
  max_retries_per_node: 0
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
tasks:
  - id: task_confirm_docs_scope
    title: Confirm audience, source-of-truth code paths, and the exact docs surfaces that need updating.
    completed: false
    routing:
      cell: seq_pre
      tags: [docs, planning]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Audience, source-of-truth code/tests, and required doc surfaces are explicit.
        - Scope separates factual documentation updates from product or runtime changes.
  - id: task_update_docs_and_examples
    title: Update the targeted docs, examples, and operator guidance to match current behavior.
    completed: false
    deps: [task_confirm_docs_scope]
    routing:
      cell: par_core
      tags: [docs, authoring]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Updated docs reflect code-and-test truth instead of inferred intent.
        - Replace this placeholder with the doc checks, screenshots, or generated outputs that matter here.
  - id: task_verify_docs_consistency
    title: Verify commands, links, terminology, and cross-doc consistency before sign-off.
    completed: false
    deps: [task_update_docs_and_examples]
    routing:
      cell: seq_post
      tags: [docs, verification]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Validation confirms commands, links, and naming stay coherent across the edited docs set.
        - Final notes call out any intentionally deferred doc debt or follow-up ADR work.
orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: prepare_dispatch_default
      kind: prepare_dispatch
      agent: orchestrator_default
      enabled: true
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
    - id: summarize_default
      kind: summarize_work
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
outputs:
  required_artifacts:
    - id: final_report
      format: markdown
    - id: machine_bundle
      format: json
"""

STARTER_RELEASE_PREP = """\
# Starter template for release preparation.
# Use this when you need a coordinated release-readiness pass across deterministic gates and a fresh cold-start check.
# Customize first: plan_id, name, lane names if needed, and the exact commands/artifacts that represent your release bar.
# Task titles assume one release candidate that needs scope confirmation, parallel validation, and a sign-off summary.
version: 1
plan_id: starter_release_prep
name: Starter Release Prep
materials:
  autodiscover:
    enabled: true
    path: .
    include_globs:
      - '**/*.yaml'
      - '**/*.yml'
      - '**/*.md'
      - '**/*.txt'
  includes: []
  uploads: []
constraints:
  max_runtime_seconds: 3600
  max_total_steps: 120
  max_cost_usd: 10.0
  fail_fast: true
  max_parallel: 3
  acceptance_timeout_seconds: 120
  max_retries_per_node: 0
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
tasks:
  - id: task_define_release_scope
    title: Confirm release scope, version intent, blockers, and the sign-off bar before running gates.
    completed: false
    routing:
      group: trunk
      tags: [release, planning]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Release scope, blockers, and required sign-off criteria are explicit.
        - The run defines what would fail the release instead of treating checks as advisory.
  - id: task_run_deterministic_gates
    title: Run deterministic quality gates and capture pass or fail evidence for the release candidate.
    completed: false
    deps: [task_define_release_scope]
    routing:
      lane: lane_gates
      tags: [release, gates]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Replace this placeholder with the lint, test, or policy commands required for your release bar.
        - Evidence is sufficient for another maintainer to audit what passed, failed, or was waived.
  - id: task_run_cold_start_check
    title: Run a fresh-workspace or first-run verification pass and capture operator-facing results.
    completed: false
    deps: [task_define_release_scope]
    routing:
      lane: lane_cold_start
      tags: [release, cold-start]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Replace this placeholder with the cold-start or smoke-flow command that represents release readiness.
        - Operator-facing output is reviewed for clarity, not just exit-code success.
  - id: task_publish_release_summary
    title: Merge gate evidence into one release summary with pass or fail status, waivers, and next actions.
    completed: false
    deps: [task_run_deterministic_gates, task_run_cold_start_check]
    routing:
      group: trunk
      cell: trunk_post
      tags: [release, signoff]
    acceptance:
      commands: []
      required_artifacts: []
      rubric:
        - Final summary states whether the release candidate is ready, blocked, or conditionally approved.
        - Remaining risks, waivers, and follow-up owners are explicit instead of implied.
orchestration:
  template: branched
  inference_mode: mixed
  behaviors:
    - id: prepare_dispatch_default
      kind: prepare_dispatch
      agent: orchestrator_default
      enabled: true
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
    - id: summarize_default
      kind: summarize_work
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_gates, lane_cold_start]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
outputs:
  required_artifacts:
    - id: final_report
      format: markdown
    - id: machine_bundle
      format: json
"""
