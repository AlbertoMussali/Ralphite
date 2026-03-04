export type AgentRole = "worker" | "orchestrator_pre" | "orchestrator_post";
export type TaskSourceKind = "markdown_checklist";

export interface PlanSpecV3 {
  version: 3;
  plan_id: string;
  name: string;
  workspace?: { root?: string | null };
  materials?: {
    autodiscover?: { enabled?: boolean; path?: string; include_globs?: string[] };
    includes?: string[];
    uploads?: string[];
  };
  task_source?: TaskSourceSpec;
  agent_profiles: AgentProfileSpec[];
  execution_structure: ExecutionStructureSpec;
  constraints?: ConstraintsSpecV3;
  outputs?: OutputsSpec;
}

export interface OutputsSpec {
  required_artifacts?: Array<{ id: string; format: string }>;
}

export interface TaskSourceSpec {
  kind?: TaskSourceKind;
  path?: string;
  parser_version?: number;
}

export interface AgentProfileSpec {
  id: string;
  role: AgentRole;
  provider: string;
  model: string;
  system_prompt?: string;
  tools_allow?: string[];
}

export interface OrchestratorStepSpec {
  enabled?: boolean;
  agent_profile_id?: string;
}

export interface PhaseExecutionSpec {
  id: string;
  label?: string;
  pre_orchestrator?: OrchestratorStepSpec;
  post_orchestrator?: OrchestratorStepSpec;
}

export interface ExecutionStructureSpec {
  phases: PhaseExecutionSpec[];
}

export interface ConstraintsSpecV3 {
  max_runtime_seconds?: number;
  max_total_steps?: number;
  max_cost_usd?: number;
  fail_fast?: boolean;
  max_parallel?: number;
}

export type PlanSpec = PlanSpecV3;

export interface EventEnvelope {
  ts: string;
  run_id: string;
  group?: string | null;
  task_id?: string | null;
  stage: string;
  event: string;
  level: "info" | "warn" | "error" | string;
  message: string;
  meta: Record<string, unknown>;
}
