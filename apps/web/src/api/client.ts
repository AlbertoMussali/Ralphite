import type {
  AuthTokens,
  BootstrapPayload,
  PlanContentResponse,
  PlanFile,
  Run,
  RunBundle,
  RunDetail,
  RunEvent,
  ToolPolicy,
  ValidationResponse,
} from "../types/api";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";
const TOKEN_KEY = "ralphite.access_token";

function extractApiError(status: number, body: unknown): string {
  if (typeof body === "string" && body.trim()) return body;

  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first === "object" && "msg" in first) {
        return String((first as { msg: unknown }).msg);
      }
      return JSON.stringify(detail);
    }
    if (detail && typeof detail === "object" && "issues" in (detail as Record<string, unknown>)) {
      return "Plan validation failed. Fix issues in validation panel.";
    }
    return JSON.stringify(detail);
  }

  return `HTTP ${status}`;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (!token) {
    localStorage.removeItem(TOKEN_KEY);
    return;
  }
  localStorage.setItem(TOKEN_KEY, token);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      credentials: "include",
    });
  } catch (_error) {
    throw new Error("Network error: unable to reach API service.");
  }

  if (!response.ok) {
    const contentType = response.headers.get("content-type") ?? "";
    let body: unknown = null;
    if (contentType.includes("application/json")) {
      body = await response.json();
    } else {
      body = await response.text();
    }
    throw new Error(extractApiError(response.status, body));
  }

  if (response.status === 204) return {} as T;
  return (await response.json()) as T;
}

export async function getHealth(): Promise<{ ok: boolean; version?: string }> {
  return request<{ ok: boolean; version?: string }>("/health");
}

export async function signup(email: string, password: string): Promise<AuthTokens> {
  return request<AuthTokens>("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function login(email: string, password: string): Promise<AuthTokens> {
  return request<AuthTokens>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function getBootstrap(): Promise<BootstrapPayload> {
  return request<BootstrapPayload>("/bootstrap");
}

export async function ensureDefaultProject(): Promise<{ id: string; name: string }> {
  return request<{ id: string; name: string }>("/projects/default", { method: "POST" });
}

export async function getAvailableWorkspaces() {
  return request<BootstrapPayload["runner_candidates"]>("/workspaces/available");
}

export async function connectRunnerWorkspace(projectId: string, runnerId: string) {
  return request(`/projects/${projectId}/workspace/connect-runner`, {
    method: "POST",
    body: JSON.stringify({ runner_id: runnerId }),
  });
}

export async function getPlans(projectId: string): Promise<PlanFile[]> {
  return request<PlanFile[]>(`/projects/${projectId}/plans/discovered`);
}

export async function getPlanContent(projectId: string, planFileId: string): Promise<PlanContentResponse> {
  return request<PlanContentResponse>(`/projects/${projectId}/plans/${planFileId}/content`);
}

export async function validatePlan(projectId: string, content: string): Promise<ValidationResponse> {
  return request<ValidationResponse>(`/projects/${projectId}/plans/validate`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function seedStarterPlan(projectId: string): Promise<PlanFile> {
  return request<PlanFile>(`/projects/${projectId}/plans/seed-starter`, { method: "POST" });
}

export async function saveVersionedPlan(projectId: string, plan: Record<string, unknown>, filenameHint?: string): Promise<PlanFile> {
  return request<PlanFile>(`/projects/${projectId}/plans/save-versioned`, {
    method: "POST",
    body: JSON.stringify({ plan, filename_hint: filenameHint ?? null }),
  });
}

export async function getCapabilities(projectId: string): Promise<Record<string, unknown>> {
  return request(`/projects/${projectId}/capabilities`);
}

export async function getToolPolicy(projectId: string): Promise<ToolPolicy> {
  return request<ToolPolicy>(`/projects/${projectId}/tool-policy`);
}

export async function updateToolPolicy(
  projectId: string,
  payload: { allow_tools: string[]; deny_tools: string[]; allow_mcps: string[]; deny_mcps: string[] }
): Promise<ToolPolicy> {
  return request<ToolPolicy>(`/projects/${projectId}/tool-policy`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function createRun(projectId: string, payload: { plan_file_id?: string; plan_content?: string }): Promise<Run> {
  return request<Run>(`/projects/${projectId}/runs`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getRun(projectId: string, runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/projects/${projectId}/runs/${runId}`);
}

export async function cancelRun(projectId: string, runId: string): Promise<Record<string, unknown>> {
  return request(`/projects/${projectId}/runs/${runId}/cancel`, { method: "POST" });
}

export async function getBundle(projectId: string, runId: string): Promise<RunBundle> {
  return request<RunBundle>(`/projects/${projectId}/runs/${runId}/bundle`);
}

export function streamRunEvents(
  projectId: string,
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError: (err: Event) => void
): EventSource {
  const source = new EventSource(`${API_BASE}/projects/${projectId}/runs/${runId}/events`, { withCredentials: true });
  source.addEventListener("run_event", (evt) => {
    const parsed = JSON.parse((evt as MessageEvent).data) as RunEvent;
    onEvent(parsed);
  });
  source.onerror = onError;
  return source;
}
