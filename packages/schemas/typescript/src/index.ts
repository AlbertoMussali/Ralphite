export type NodeKind = "agent" | "gate";
export type EdgeWhen = "success" | "failure" | "retry" | "always";

export interface PlanSpecV1 {
  version: 1;
  plan_id: string;
  name: string;
  workspace?: { root?: string | null };
  materials?: {
    autodiscover?: { enabled?: boolean; path?: string; include_globs?: string[] };
    includes?: string[];
    uploads?: string[];
  };
  agents: AgentSpec[];
  graph: GraphSpec;
  constraints?: ConstraintsSpec;
  outputs?: OutputsSpec;
}

export interface AgentSpec {
  id: string;
  provider: string;
  model: string;
  system_prompt?: string;
  tools_allow?: string[];
}

export interface GateSpec {
  mode: string;
  pass_if: string;
}

export interface NodeSpec {
  id: string;
  kind: NodeKind;
  group?: string;
  depends_on?: string[];
  agent_id?: string | null;
  task?: string | null;
  gate?: GateSpec | null;
}

export interface EdgeSpec {
  from: string;
  to: string;
  when: EdgeWhen;
  loop_id?: string | null;
}

export interface LoopSpec {
  id: string;
  max_iterations: number;
}

export interface GraphSpec {
  nodes: NodeSpec[];
  edges?: EdgeSpec[];
  loops?: LoopSpec[];
}

export interface ConstraintsSpec {
  max_runtime_seconds?: number;
  max_total_steps?: number;
  max_cost_usd?: number;
  fail_fast?: boolean;
}

export interface OutputsSpec {
  required_artifacts?: Array<{ id: string; format: string }>;
}

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
