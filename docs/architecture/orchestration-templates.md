# Orchestration Templates

Owners: engine, schemas
Last verified against commit: 70b0c1f

Source files:

- `src/ralphite/engine/structure_compiler.py`
- `src/ralphite/engine/task_parser.py`
- `src/ralphite/schemas/plan.py`

## Supported Templates

- `general_sps`
- `branched`
- `blue_red`
- `custom`

## Compilation Model

1. Parse task graph and validate dependencies.
2. Resolve template cells + behavior bindings.
3. Expand runtime nodes (workers + orchestrators).
4. Validate DAG and emit execution levels.

## Behavior Kinds

- `prepare_dispatch`
- `merge_and_conflict_resolution`
- `summarize_work`
- `custom`

## Routing Notes

- explicit `routing.cell` and `routing.lane` take precedence
- template constraints are enforced by validation and compile phases
