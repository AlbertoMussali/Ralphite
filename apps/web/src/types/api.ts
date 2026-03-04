export interface AuthTokens {
  access_token: string;
  token_type: string;
  csrf_token: string;
}

export interface User {
  id: string;
  email: string;
  settings_json: Record<string, unknown>;
  created_at?: string;
}

export interface Project {
  id: string;
  name: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceStatus {
  status: string;
  workspace_root: string | null;
  connected_runner_id: string | null;
}

export interface RunnerCandidate {
  runner_id: string;
  workspace_root: string;
  status: string;
  runner_version: string;
  last_heartbeat_at: string | null;
}

export interface BootstrapPayload {
  user: User;
  default_project_id: string;
  workspace_status: WorkspaceStatus;
  runner_candidates: RunnerCandidate[];
}

export interface PlanFile {
  id: string;
  source: string;
  origin: string;
  path: string;
  filename: string;
  checksum_sha256: string;
  version_label: string | null;
  modified_at: string | null;
  updated_at: string;
}

export interface PlanContentResponse {
  id: string;
  path: string;
  content: string;
}

export interface ValidationIssue {
  code: string;
  message: string;
  path: string;
  level: string;
  hint?: string | null;
}

export interface ValidationSummary {
  plan_id?: string;
  name?: string;
  nodes: number;
  edges: number;
  agent_nodes: number;
  gate_nodes: number;
  groups: Record<string, number>;
  loops: Array<{ id: string; max_iterations: number }>;
  parallel_sets: Array<{ level: number; nodes: string[] }>;
  constraints: Record<string, unknown>;
  required_tools: string[];
  required_mcps: string[];
}

export interface ValidationDiagnostics {
  empty_plan: boolean;
  no_agent_nodes: boolean;
  no_outputs: boolean;
  no_retry_loop: boolean;
  single_node_only: boolean;
  readable_messages: string[];
}

export interface ValidationResponse {
  valid: boolean;
  issues: ValidationIssue[];
  summary: ValidationSummary;
  diagnostics: ValidationDiagnostics;
}

export interface ToolPolicy {
  project_id: string;
  allow_tools: string[];
  deny_tools: string[];
  allow_mcps: string[];
  deny_mcps: string[];
  updated_at: string;
}

export interface Run {
  id: string;
  project_id: string;
  status: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunDetail {
  id: string;
  project_id: string;
  status: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  nodes: Array<Record<string, unknown>>;
}

export interface RunEvent {
  id?: number;
  ts?: string;
  run_id?: string;
  group: string | null;
  task_id: string | null;
  stage: string;
  event: string;
  level: string;
  message: string;
  meta: Record<string, unknown>;
}

export interface RunBundle {
  run_id: string;
  artifacts: Array<{
    id: string;
    artifact_id: string;
    format: string;
    content: string;
    created_at: string;
  }>;
}
