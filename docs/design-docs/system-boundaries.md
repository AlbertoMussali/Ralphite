# System Boundaries

Owners: engine, schemas, tui
Last verified against commit: 70b0c1f

## In Scope

- Parsing/validating v5 plans
- Compiling orchestration templates into runtime DAG nodes
- Executing nodes through headless backends (, optional )
- Enforcing acceptance commands/artifact checks
- Managing run lifecycle, persistence, recovery, and operator UX

## Out of Scope

- Hosting remote orchestration services
- Non-v5 plan runtime compatibility
- Hidden implicit task-generation semantics outside explicit plan + template contracts
