import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Background,
  Connection,
  Controls,
  Edge,
  EdgeChange,
  Node,
  NodeChange,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from "reactflow";
import "reactflow/dist/style.css";
import { Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import type { PlanSpecV1 } from "@ralphite/schemas";
import YAML from "yaml";
import SectionCard from "./components/SectionCard";
import {
  cancelRun,
  connectRunnerWorkspace,
  createRun,
  ensureDefaultProject,
  getBootstrap,
  getBundle,
  getCapabilities,
  getHealth,
  getPlanContent,
  getPlans,
  getRun,
  getToolPolicy,
  getToken,
  login,
  saveVersionedPlan,
  seedStarterPlan,
  setToken,
  signup,
  streamRunEvents,
  updateToolPolicy,
  validatePlan,
} from "./api/client";
import type {
  BootstrapPayload,
  PlanFile,
  RunBundle,
  RunDetail,
  RunEvent,
  ToolPolicy,
  ValidationResponse,
} from "./types/api";

type BuilderNodeKind = "start" | "end" | "agent" | "gate" | "loop" | "split" | "join";

interface BuilderNodeData {
  nodeKind: BuilderNodeKind;
  label: string;
  group: string;
  agentId?: string;
  task?: string;
  gateMode?: string;
  passIf?: string;
}

interface BuilderEdgeData {
  when: "success" | "failure" | "retry" | "always";
  loopId?: string;
}

interface LoopConfig {
  id: string;
  max_iterations: number;
}

function nodeStyleForKind(kind: BuilderNodeKind): React.CSSProperties {
  if (kind === "start" || kind === "end") {
    return {
      borderColor: "#ff3d00",
      color: "#ff3d00",
      fontWeight: 700,
      minWidth: 100,
    };
  }
  if (kind === "gate") {
    return {
      borderColor: "#f59e0b",
      minWidth: 130,
    };
  }
  if (kind === "loop" || kind === "split" || kind === "join") {
    return {
      borderColor: "#4b5563",
      minWidth: 130,
    };
  }
  return {
    borderColor: "#262626",
    minWidth: 130,
  };
}

function createBaseNodes(): Array<Node<BuilderNodeData>> {
  return [
    {
      id: "start",
      position: { x: 220, y: 30 },
      data: { nodeKind: "start", label: "Start", group: "meta" },
      draggable: true,
      type: "default",
      style: nodeStyleForKind("start"),
    },
    {
      id: "end",
      position: { x: 220, y: 560 },
      data: { nodeKind: "end", label: "End", group: "meta" },
      draggable: true,
      type: "default",
      style: nodeStyleForKind("end"),
    },
  ];
}

function createDefaultRalphLoopState(): {
  nodes: Array<Node<BuilderNodeData>>;
  edges: Array<Edge<BuilderEdgeData>>;
  loops: LoopConfig[];
} {
  const baseNodes = createBaseNodes();

  const plannerNode: Node<BuilderNodeData> = {
    id: "n_plan",
    position: { x: 220, y: 150 },
    data: {
      nodeKind: "agent",
      label: "Planner",
      group: "planning",
      agentId: "planner",
      task: "Decompose goal into executable tasks.",
    },
    type: "default",
    draggable: true,
    style: nodeStyleForKind("agent"),
  };

  const executeNode: Node<BuilderNodeData> = {
    id: "n_execute",
    position: { x: 220, y: 280 },
    data: {
      nodeKind: "agent",
      label: "Execution",
      group: "execution",
      agentId: "worker",
      task: "Execute tasks and update artifacts.",
    },
    type: "default",
    draggable: true,
    style: nodeStyleForKind("agent"),
  };

  const gateNode: Node<BuilderNodeData> = {
    id: "n_gate",
    position: { x: 220, y: 410 },
    data: {
      nodeKind: "gate",
      label: "Quality Gate",
      group: "gate",
      gateMode: "rubric",
      passIf: "all_acceptance_checks_pass",
    },
    type: "default",
    draggable: true,
    style: nodeStyleForKind("gate"),
  };

  const defaultEdges: Array<Edge<BuilderEdgeData>> = [
    {
      id: "e_start_plan",
      source: "start",
      target: "n_plan",
      data: { when: "always" },
      label: "always",
      animated: true,
    },
    {
      id: "e_plan_exec",
      source: "n_plan",
      target: "n_execute",
      data: { when: "success" },
      label: "success",
      animated: true,
    },
    {
      id: "e_exec_gate",
      source: "n_execute",
      target: "n_gate",
      data: { when: "success" },
      label: "success",
      animated: true,
    },
    {
      id: "e_gate_exec_retry",
      source: "n_gate",
      target: "n_execute",
      data: { when: "retry", loopId: "main_loop" },
      label: "retry",
      animated: true,
    },
    {
      id: "e_gate_end",
      source: "n_gate",
      target: "end",
      data: { when: "success" },
      label: "success",
      animated: true,
    },
  ];

  return {
    nodes: [...baseNodes, plannerNode, executeNode, gateNode],
    edges: defaultEdges,
    loops: [{ id: "main_loop", max_iterations: 3 }],
  };
}

function buildBuilderStateFromPlan(plan: PlanSpecV1): {
  nodes: Array<Node<BuilderNodeData>>;
  edges: Array<Edge<BuilderEdgeData>>;
  loops: LoopConfig[];
} {
  const baseNodes = createBaseNodes();
  const graphNodes = plan.graph.nodes ?? [];
  const graphEdges = plan.graph.edges ?? [];
  const graphLoops = plan.graph.loops ?? [];

  const builderNodes: Array<Node<BuilderNodeData>> = graphNodes.map((node, index) => {
    const kind: BuilderNodeKind = node.kind === "gate" ? "gate" : "agent";
    return {
      id: node.id,
      position: { x: 220, y: 140 + index * 120 },
      data: {
        nodeKind: kind,
        label: kind === "gate" ? `Gate: ${node.id}` : node.agent_id ? `Agent: ${node.agent_id}` : `Agent: ${node.id}`,
        group: node.group ?? (kind === "gate" ? "gate" : "execution"),
        agentId: node.agent_id ?? undefined,
        task: node.task ?? undefined,
        gateMode: node.gate?.mode ?? undefined,
        passIf: node.gate?.pass_if ?? undefined,
      },
      draggable: true,
      type: "default",
      style: nodeStyleForKind(kind),
    };
  });

  const edges: Array<Edge<BuilderEdgeData>> = graphEdges.map((edge, index) => {
    const data: BuilderEdgeData = {
      when: (edge.when ?? "success") as BuilderEdgeData["when"],
      ...(edge.loop_id ? { loopId: edge.loop_id } : {}),
    };
    return {
      id: `e_${index}_${edge.from}_${edge.to}`,
      source: edge.from,
      target: edge.to,
      data,
      label: data.when,
      animated: true,
    };
  });

  const nonRetryIncoming = new Set<string>();
  const nonRetryOutgoing = new Set<string>();
  for (const edge of edges) {
    if (edge.data?.when !== "retry") {
      nonRetryIncoming.add(edge.target);
      nonRetryOutgoing.add(edge.source);
    }
  }

  for (const node of builderNodes) {
    if (!nonRetryIncoming.has(node.id)) {
      edges.push({
        id: `e_start_${node.id}`,
        source: "start",
        target: node.id,
        data: { when: "always" },
        label: "always",
        animated: true,
      });
    }
  }

  for (const node of builderNodes) {
    if (!nonRetryOutgoing.has(node.id)) {
      edges.push({
        id: `e_${node.id}_end`,
        source: node.id,
        target: "end",
        data: { when: "success" },
        label: "success",
        animated: true,
      });
    }
  }

  const loops: LoopConfig[] = graphLoops.map((loop) => ({
    id: loop.id,
    max_iterations: loop.max_iterations,
  }));

  const endNode = baseNodes.find((node) => node.id === "end");
  if (endNode) {
    endNode.position = { ...endNode.position, y: Math.max(560, 180 + builderNodes.length * 130) };
  }

  return {
    nodes: [...baseNodes, ...builderNodes],
    edges,
    loops,
  };
}

function parseCsv(input: string): string[] {
  return input
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function newNodeId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function compilePlanFromBuilder(
  nodes: Array<Node<BuilderNodeData>>,
  edges: Array<Edge<BuilderEdgeData>>,
  loops: LoopConfig[]
): Record<string, unknown> {
  const realNodeKinds = new Set<BuilderNodeKind>(["agent", "gate"]);
  const realNodes = nodes.filter((node) => realNodeKinds.has(node.data.nodeKind));
  const realNodeIds = new Set(realNodes.map((node) => node.id));

  const graphNodes = realNodes.map((node) => {
    const incoming = edges
      .filter((edge) => edge.target === node.id && edge.data?.when !== "retry" && realNodeIds.has(edge.source))
      .map((edge) => edge.source);

    if (node.data.nodeKind === "agent") {
      return {
        id: node.id,
        kind: "agent",
        group: node.data.group || "execution",
        depends_on: incoming,
        agent_id: node.data.agentId || "worker",
        task: node.data.task || "Execute work item",
      };
    }

    return {
      id: node.id,
      kind: "gate",
      group: node.data.group || "gate",
      depends_on: incoming,
      gate: {
        mode: node.data.gateMode || "rubric",
        pass_if: node.data.passIf || "all_acceptance_checks_pass",
      },
    };
  });

  const graphEdges = edges
    .filter((edge) => realNodeIds.has(edge.source) && realNodeIds.has(edge.target))
    .map((edge) => {
      const when = edge.data?.when ?? "success";
      return {
        from: edge.source,
        to: edge.target,
        when,
        ...(when === "retry" && edge.data?.loopId ? { loop_id: edge.data.loopId } : {}),
      };
    });

  const loopMap = new Map<string, number>();
  for (const loop of loops) {
    loopMap.set(loop.id, loop.max_iterations);
  }
  for (const edge of graphEdges) {
    if (edge.when === "retry" && edge.loop_id && !loopMap.has(edge.loop_id)) {
      loopMap.set(edge.loop_id, 3);
    }
  }

  return {
    version: 1,
    plan_id: "builder_plan",
    name: "Builder Plan",
    materials: {
      autodiscover: {
        enabled: true,
        path: ".ralphite/plans",
        include_globs: ["**/*.yaml", "**/*.yml", "**/*.md", "**/*.txt"],
      },
      includes: [],
      uploads: [],
    },
    agents: [
      {
        id: "planner",
        provider: "openai",
        model: "gpt-4.1-mini",
        system_prompt: "Plan and orchestrate the work.",
        tools_allow: ["tool:*", "mcp:*"],
      },
      {
        id: "worker",
        provider: "openai",
        model: "gpt-4.1",
        system_prompt: "Execute assigned tasks.",
        tools_allow: ["tool:*", "mcp:*"],
      },
      {
        id: "reviewer",
        provider: "openai",
        model: "gpt-4.1-mini",
        system_prompt: "Review outputs and quality.",
        tools_allow: ["tool:*", "mcp:*"],
      },
    ],
    graph: {
      nodes: graphNodes,
      edges: graphEdges,
      loops: Array.from(loopMap.entries()).map(([id, max_iterations]) => ({ id, max_iterations })),
    },
    constraints: {
      max_runtime_seconds: 5400,
      max_total_steps: 250,
      max_cost_usd: 25,
      fail_fast: true,
    },
    outputs: {
      required_artifacts: [
        { id: "final_report", format: "markdown" },
        { id: "machine_bundle", format: "json" },
      ],
    },
  };
}

function AuthScreen({ onAuthenticated }: { onAuthenticated: () => Promise<void> }) {
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [healthError, setHealthError] = useState("");

  function classifyAuthError(err: unknown): { kind: "connectivity" | "authentication" | "signup" | "unknown"; message: string } {
    const raw = err instanceof Error ? err.message : String(err);
    const normalized = raw.toLowerCase();

    if (normalized.includes("network error") || normalized.includes("failed to fetch")) {
      return {
        kind: "connectivity",
        message: "Connection error: backend API is not reachable.",
      };
    }
    if (normalized.includes("invalid credentials")) {
      return {
        kind: "authentication",
        message: "Authentication error: email or password is incorrect.",
      };
    }
    if (normalized.includes("email already exists")) {
      return {
        kind: "signup",
        message: "Signup error: this email is already registered.",
      };
    }

    return {
      kind: "unknown",
      message: `Request error: ${raw.replace(/^Error:\s*/, "")}`,
    };
  }

  useEffect(() => {
    getHealth()
      .then(() => setHealthError(""))
      .catch(() => {
        setHealthError("Connection error: backend API is not reachable.");
      });
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!email.includes("@")) {
      setError("Enter a valid email.");
      return;
    }

    try {
      const tokens = authMode === "login" ? await login(email, password) : await signup(email, password);
      setToken(tokens.access_token);
      setHealthError("");
      await onAuthenticated();
    } catch (err) {
      const classified = classifyAuthError(err);
      setError(classified.message);
      if (classified.kind === "connectivity") {
        setHealthError(classified.message);
      } else {
        setHealthError("");
      }
    }
  }

  return (
    <main className="app-shell">
      <section className="hero-auth">
        <p className="kicker">RALPHITE CONTROL PLANE</p>
        <h1>CONNECT WORKSPACE. DESIGN STRUCTURE. RUN WITH TRACEABILITY.</h1>
        <p className="hero-copy">
          Web-first orchestration flow with starter plans, validation diagnostics, and live execution telemetry.
        </p>
        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="field">
            <label>Email</label>
            <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
          </div>
          <div className="field">
            <label>Password</label>
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
          </div>
          <button className="btn-primary" type="submit">
            {authMode === "login" ? "LOGIN" : "SIGN UP"}
          </button>
          <button
            className="btn-ghost"
            type="button"
            onClick={() => setAuthMode((prev) => (prev === "login" ? "signup" : "login"))}
          >
            {authMode === "login" ? "Need an account? Sign up" : "Already have an account? Login"}
          </button>
        </form>
        {healthError ? <p className="error-line">Connectivity: {healthError}</p> : null}
        {error ? <p className="error-line">{error}</p> : null}
      </section>
    </main>
  );
}

function WorkspacePage({
  bootstrap,
  projectId,
  onConnected,
}: {
  bootstrap: BootstrapPayload;
  projectId: string;
  onConnected: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const [selectedRunnerId, setSelectedRunnerId] = useState(bootstrap.runner_candidates[0]?.runner_id ?? "");
  const [error, setError] = useState("");

  async function connect() {
    if (!selectedRunnerId) return;
    setError("");
    try {
      await connectRunnerWorkspace(projectId, selectedRunnerId);
      await onConnected();
      navigate("/onboarding/structure");
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <SectionCard title="Workspace Connection" kicker="01">
      <p className="muted">Select a live runner workspace. This is the only workspace used for this project in v1.</p>
      <div className="plan-list">
        {bootstrap.runner_candidates.length === 0 ? <p className="muted">No runner heartbeat detected yet.</p> : null}
        {bootstrap.runner_candidates.map((runner) => (
          <label key={runner.runner_id} className="plan-item">
            <input
              type="radio"
              checked={selectedRunnerId === runner.runner_id}
              onChange={() => setSelectedRunnerId(runner.runner_id)}
            />
            <span>{runner.workspace_root}</span>
            <code>{runner.status}</code>
          </label>
        ))}
      </div>
      <button className="btn-primary" onClick={connect} disabled={!selectedRunnerId}>
        USE WORKSPACE
      </button>
      {error ? <p className="error-line">{error}</p> : null}
    </SectionCard>
  );
}

function ValidationPanel({ validation }: { validation: ValidationResponse | null }) {
  if (!validation) return <p className="muted">Validate a plan to see diagnostics.</p>;

  return (
    <div className="validation-grid">
      <article className={validation.valid ? "validation-card valid" : "validation-card invalid"}>
        <p className="kicker">Validation</p>
        <h3>{validation.valid ? "Plan Valid" : "Plan Invalid"}</h3>
        <p className="muted">
          Nodes {validation.summary.nodes} | Agent {validation.summary.agent_nodes} | Gates {validation.summary.gate_nodes}
        </p>
      </article>
      <article className="validation-card">
        <p className="kicker">Loops & Parallelism</p>
        <p className="muted">
          Loops: {validation.summary.loops.length} | Parallel sets: {validation.summary.parallel_sets.length}
        </p>
      </article>
      <article className="validation-card">
        <p className="kicker">Heuristics</p>
        <ul className="flat-list">
          {validation.diagnostics.readable_messages.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      </article>
      <article className="validation-card">
        <p className="kicker">Issues</p>
        {validation.issues.length === 0 ? <p className="muted">No issues found.</p> : null}
        <div className="issue-list">
          {validation.issues.map((issue, index) => (
            <div key={`${issue.code}-${index}`} className="issue-item">
              <strong>{issue.code}</strong>
              <p>{issue.message}</p>
              <small>
                path: {issue.path}
                {issue.hint ? ` | hint: ${issue.hint}` : ""}
              </small>
            </div>
          ))}
        </div>
      </article>
    </div>
  );
}

function StructurePage({
  projectId,
  plans,
  selectedPlanId,
  setSelectedPlanId,
  refreshPlans,
}: {
  projectId: string;
  plans: PlanFile[];
  selectedPlanId: string;
  setSelectedPlanId: (value: string) => void;
  refreshPlans: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [seedAttempted, setSeedAttempted] = useState(false);

  useEffect(() => {
    if (plans.length > 0 || seedAttempted) return;
    setSeedAttempted(true);
    seedStarterPlan(projectId)
      .then(refreshPlans)
      .catch(() => {
        // Ignore auto-seed error; user can retry manually.
      });
  }, [plans.length, projectId, refreshPlans, seedAttempted]);

  useEffect(() => {
    if (!selectedPlanId && plans.length > 0) {
      setSelectedPlanId(plans[0].id);
    }
  }, [plans, selectedPlanId, setSelectedPlanId]);

  async function validateSelectedPlan() {
    if (!selectedPlanId) return;
    setLoading(true);
    setError("");
    try {
      const content = await getPlanContent(projectId, selectedPlanId);
      const result = await validatePlan(projectId, content.content);
      setValidation(result);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <SectionCard
      title="Plan Selection & Validation"
      kicker="02"
      action={
        <div className="stack-inline">
          <button className="btn-ghost" onClick={refreshPlans}>
            Refresh
          </button>
          <button className="btn-outline" onClick={() => navigate("/onboarding/structure/builder")}>
            Open Builder
          </button>
        </div>
      }
    >
      <div className="plan-list">
        {plans.length === 0 ? <p className="muted">No plans discovered yet. Starter seeding should appear shortly.</p> : null}
        {plans.map((plan) => (
          <div
            key={plan.id}
            className="plan-item clickable"
            role="button"
            tabIndex={0}
            onClick={() => {
              setSelectedPlanId(plan.id);
              navigate(`/onboarding/structure/builder/${plan.id}`);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setSelectedPlanId(plan.id);
                navigate(`/onboarding/structure/builder/${plan.id}`);
              }
            }}
          >
            <input
              type="radio"
              checked={selectedPlanId === plan.id}
              onChange={() => setSelectedPlanId(plan.id)}
            />
            <span>{plan.path}</span>
            <code>{plan.checksum_sha256.slice(0, 8)}</code>
          </div>
        ))}
      </div>
      <div className="stack-inline">
        <button className="btn-primary" onClick={validateSelectedPlan} disabled={!selectedPlanId || loading}>
          {loading ? "Validating..." : "Validate Selected Plan"}
        </button>
        <button className="btn-outline" onClick={() => navigate("/run")} disabled={!selectedPlanId}>
          Continue to Run
        </button>
      </div>
      {error ? <p className="error-line">{error}</p> : null}
      <ValidationPanel validation={validation} />
    </SectionCard>
  );
}

function BuilderPage({
  projectId,
  onPlanSaved,
}: {
  projectId: string;
  onPlanSaved: (plan: PlanFile) => Promise<void>;
}) {
  const navigate = useNavigate();
  const { planId } = useParams();
  const [nodes, setNodes] = useState<Array<Node<BuilderNodeData>>>(createBaseNodes);
  const [edges, setEdges] = useState<Array<Edge<BuilderEdgeData>>>([]);
  const [loops, setLoops] = useState<LoopConfig[]>([{ id: "main_loop", max_iterations: 3 }]);
  const [selectedNodeId, setSelectedNodeId] = useState<string>("");
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>("");
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState("Builder ready.");
  const [saving, setSaving] = useState(false);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const compiledPlan = compilePlanFromBuilder(nodes, edges, loops);
  const compiledYaml = YAML.stringify(compiledPlan);

  useEffect(() => {
    if (!planId) return;
    const selectedPlanId = planId;

    async function loadPlanInBuilder() {
      try {
        const content = await getPlanContent(projectId, selectedPlanId);
        const parsed = YAML.parse(content.content) as PlanSpecV1;
        if (!parsed?.graph || !Array.isArray(parsed.graph.nodes)) {
          throw new Error("selected file is not a valid PlanSpecV1 graph");
        }

        const builderState = buildBuilderStateFromPlan(parsed);
        setNodes(builderState.nodes);
        setEdges(builderState.edges);
        setLoops(builderState.loops);
        setSelectedNodeId("");
        setSelectedEdgeId("");
        setValidation(null);
        setError("");
        setActivity(`Loaded ${parsed.plan_id} in builder.`);
      } catch (err) {
        setError(`Failed to load selected plan in builder: ${String(err)}`);
      }
    }

    void loadPlanInBuilder();
  }, [planId, projectId]);

  function addBuilderNode(kind: BuilderNodeKind) {
    const node: Node<BuilderNodeData> = {
      id: newNodeId(kind),
      position: { x: 150 + Math.random() * 240, y: 110 + Math.random() * 340 },
      data: {
        nodeKind: kind,
        label:
          kind === "agent"
            ? "Agent"
            : kind === "gate"
              ? "Gate"
              : kind === "loop"
                ? "Loop Block"
                : kind === "split"
                  ? "Parallel Split"
                  : kind === "join"
                    ? "Parallel Join"
                    : kind === "start"
                      ? "Start"
                      : "End",
        group: kind === "agent" ? "execution" : kind === "gate" ? "gate" : "meta",
        agentId: kind === "agent" ? "worker" : undefined,
        task: kind === "agent" ? "Describe task" : undefined,
        gateMode: kind === "gate" ? "rubric" : undefined,
        passIf: kind === "gate" ? "all_acceptance_checks_pass" : undefined,
      },
      type: "default",
      draggable: true,
      style: nodeStyleForKind(kind),
    };
    setNodes((prev) => [...prev, node]);
    setActivity(`Added ${node.data.label} node.`);
  }

  function resetCanvasToStartEnd() {
    setNodes(createBaseNodes());
    setEdges([]);
    setLoops([]);
    setSelectedNodeId("");
    setSelectedEdgeId("");
    setValidation(null);
    setError("");
    setActivity("Canvas reset to Start/End.");
  }

  function loadDefaultRalphLoop() {
    const preset = createDefaultRalphLoopState();
    setNodes(preset.nodes);
    setEdges(preset.edges);
    setLoops(preset.loops);
    setSelectedNodeId("");
    setSelectedEdgeId("");
    setValidation(null);
    setError("");
    setActivity("Loaded default Ralph loop.");
  }

  function onNodesChange(changes: NodeChange[]) {
    setNodes((current) => applyNodeChanges(changes, current));
  }

  function onEdgesChange(changes: EdgeChange[]) {
    setEdges((current) => applyEdgeChanges(changes, current));
  }

  function onConnect(connection: Connection) {
    if (!connection.source || !connection.target) return;
    const edge: Edge<BuilderEdgeData> = {
      id: newNodeId("edge"),
      source: connection.source,
      target: connection.target,
      data: { when: "success" },
      label: "success",
      animated: true,
    };
    setEdges((current) => addEdge(edge, current));
    setActivity(`Connected ${connection.source} -> ${connection.target}.`);
  }

  function patchSelectedNode<K extends keyof BuilderNodeData>(key: K, value: BuilderNodeData[K]) {
    if (!selectedNode) return;
    setNodes((current) =>
      current.map((node) =>
        node.id === selectedNode.id
          ? {
              ...node,
              data: { ...node.data, [key]: value },
            }
          : node
      )
    );
  }

  function patchSelectedEdge(data: Partial<BuilderEdgeData>) {
    if (!selectedEdge) return;
    setEdges((current) =>
      current.map((edge) => {
        if (edge.id !== selectedEdge.id) return edge;
        const nextData = { ...(edge.data ?? { when: "success" }), ...data };
        return { ...edge, data: nextData, label: nextData.when };
      })
    );
  }

  async function validateCompiled() {
    setError("");
    try {
      const result = await validatePlan(projectId, compiledYaml);
      setValidation(result);
      setActivity(result.valid ? "Validation passed." : "Validation found issues.");
    } catch (err) {
      setError(String(err));
    }
  }

  async function saveCompiled() {
    setSaving(true);
    setError("");
    try {
      const result = await validatePlan(projectId, compiledYaml);
      setValidation(result);
      if (!result.valid) {
        setError("Plan is invalid. Fix issues before saving.");
        setActivity("Save blocked: validation failed.");
        return;
      }
      const plan = await saveVersionedPlan(projectId, compiledPlan, "builder");
      await onPlanSaved(plan);
      setActivity(`Saved ${plan.filename}.`);
      navigate("/onboarding/structure");
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard title="Visual Structure Builder" kicker="Builder">
      <div className="builder-toolbar">
        <button className="btn-outline" onClick={resetCanvasToStartEnd}>
          Reset Canvas
        </button>
        <button className="btn-outline" onClick={loadDefaultRalphLoop}>
          Load Default Ralph Loop
        </button>
        <button className="btn-outline" onClick={() => addBuilderNode("agent")}>
          Agent
        </button>
        <button className="btn-outline" onClick={() => addBuilderNode("gate")}>
          Gate
        </button>
        <button className="btn-ghost" onClick={() => addBuilderNode("loop")}>
          Loop Block
        </button>
        <button className="btn-ghost" onClick={() => addBuilderNode("split")}>
          Parallel Split
        </button>
        <button className="btn-ghost" onClick={() => addBuilderNode("join")}>
          Parallel Join
        </button>
        <button className="btn-primary" onClick={validateCompiled}>
          Validate
        </button>
        <button className="btn-primary" onClick={saveCompiled} disabled={saving}>
          {saving ? "Saving..." : "Save Versioned Plan"}
        </button>
      </div>
      <div className="builder-meta">
        <p className="muted">Nodes: {nodes.length}</p>
        <p className="muted">Edges: {edges.length}</p>
        <p className="muted">{activity}</p>
      </div>

      <div className="builder-layout">
        <div className="builder-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onPaneClick={() => {
              setSelectedNodeId("");
              setSelectedEdgeId("");
            }}
            onNodeClick={(_event, node: Node<BuilderNodeData>) => setSelectedNodeId(node.id)}
            onEdgeClick={(_event, edge: Edge<BuilderEdgeData>) => setSelectedEdgeId(edge.id)}
            fitView
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
        <aside className="builder-panel">
          <h3>Inspector</h3>
          {selectedNode ? (
            <div className="field-grid">
              <label>Node Label</label>
              <input value={selectedNode.data.label} onChange={(event) => patchSelectedNode("label", event.target.value)} />
              <label>Group</label>
              <input value={selectedNode.data.group} onChange={(event) => patchSelectedNode("group", event.target.value)} />
              {selectedNode.data.nodeKind === "agent" ? (
                <>
                  <label>Agent ID</label>
                  <input
                    value={selectedNode.data.agentId ?? "worker"}
                    onChange={(event) => patchSelectedNode("agentId", event.target.value)}
                  />
                  <label>Task</label>
                  <textarea
                    value={selectedNode.data.task ?? ""}
                    onChange={(event) => patchSelectedNode("task", event.target.value)}
                    rows={4}
                  />
                </>
              ) : null}
              {selectedNode.data.nodeKind === "gate" ? (
                <>
                  <label>Gate Mode</label>
                  <input
                    value={selectedNode.data.gateMode ?? "rubric"}
                    onChange={(event) => patchSelectedNode("gateMode", event.target.value)}
                  />
                  <label>Pass If</label>
                  <input
                    value={selectedNode.data.passIf ?? "all_acceptance_checks_pass"}
                    onChange={(event) => patchSelectedNode("passIf", event.target.value)}
                  />
                </>
              ) : null}
            </div>
          ) : (
            <p className="muted">Select a node to edit.</p>
          )}

          <h3>Edge Control</h3>
          {selectedEdge ? (
            <div className="field-grid">
              <label>When</label>
              <select
                value={selectedEdge.data?.when ?? "success"}
                onChange={(event) =>
                  patchSelectedEdge({ when: event.target.value as BuilderEdgeData["when"] })
                }
              >
                <option value="success">success</option>
                <option value="failure">failure</option>
                <option value="retry">retry</option>
                <option value="always">always</option>
              </select>
              {selectedEdge.data?.when === "retry" ? (
                <>
                  <label>Loop ID</label>
                  <input
                    value={selectedEdge.data.loopId ?? "main_loop"}
                    onChange={(event) => patchSelectedEdge({ loopId: event.target.value })}
                  />
                </>
              ) : null}
            </div>
          ) : (
            <p className="muted">Select an edge to configure conditions.</p>
          )}

          <h3>Loops</h3>
          <div className="loop-list">
            {loops.map((loop, index) => (
              <div key={`${loop.id}-${index}`} className="loop-item">
                <input
                  value={loop.id}
                  onChange={(event) =>
                    setLoops((current) =>
                      current.map((row, rowIndex) => (rowIndex === index ? { ...row, id: event.target.value } : row))
                    )
                  }
                />
                <input
                  type="number"
                  value={loop.max_iterations}
                  onChange={(event) =>
                    setLoops((current) =>
                      current.map((row, rowIndex) =>
                        rowIndex === index ? { ...row, max_iterations: Math.max(1, Number(event.target.value) || 1) } : row
                      )
                    )
                  }
                />
              </div>
            ))}
            <button className="btn-ghost" onClick={() => setLoops((current) => [...current, { id: newNodeId("loop"), max_iterations: 3 }])}>
              Add Loop
            </button>
          </div>
        </aside>
      </div>

      <SectionCard title="Compiled YAML Preview">
        <pre className="json-box">{compiledYaml}</pre>
      </SectionCard>
      <ValidationPanel validation={validation} />
      {error ? <p className="error-line">{error}</p> : null}
    </SectionCard>
  );
}

function RunPage({ projectId, selectedPlanId }: { projectId: string; selectedPlanId: string }) {
  const navigate = useNavigate();
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    async function loadPreview() {
      if (!selectedPlanId) return;
      try {
        const content = await getPlanContent(projectId, selectedPlanId);
        const result = await validatePlan(projectId, content.content);
        setValidation(result);
      } catch (err) {
        setError(String(err));
      }
    }
    void loadPreview();
  }, [projectId, selectedPlanId]);

  async function startRun() {
    if (!selectedPlanId) return;
    setLoading(true);
    setError("");
    try {
      const run = await createRun(projectId, { plan_file_id: selectedPlanId });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  if (!selectedPlanId) return <Navigate to="/onboarding/structure" replace />;

  return (
    <SectionCard title="Run Preview & Execute" kicker="03">
      {validation ? (
        <div className="stats-grid">
          <article>
            <span>TASKS READ</span>
            <strong>{validation.summary.nodes}</strong>
          </article>
          <article>
            <span>PARALLEL LEVELS</span>
            <strong>{validation.summary.parallel_sets.length}</strong>
          </article>
          <article>
            <span>LOOP CAPS</span>
            <strong>{validation.summary.loops.map((loop) => loop.max_iterations).join(", ") || "0"}</strong>
          </article>
          <article>
            <span>REQUIRED TOOLS</span>
            <strong>{validation.summary.required_tools.length + validation.summary.required_mcps.length}</strong>
          </article>
        </div>
      ) : (
        <p className="muted">Loading execution preview...</p>
      )}
      <button className="btn-primary" onClick={startRun} disabled={loading || !validation?.valid}>
        {loading ? "Starting..." : "Start Run"}
      </button>
      {!validation?.valid ? <p className="muted">Validation must pass before run start.</p> : null}
      {error ? <p className="error-line">{error}</p> : null}
      <ValidationPanel validation={validation} />
    </SectionCard>
  );
}

function RunDetailPage({ projectId }: { projectId: string }) {
  const { runId = "" } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [bundle, setBundle] = useState<RunBundle | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!runId) return;
    getRun(projectId, runId)
      .then(setRun)
      .catch((err) => setError(String(err)));
  }, [projectId, runId]);

  useEffect(() => {
    if (!runId) return;
    const source = streamRunEvents(
      projectId,
      runId,
      (event) => {
        setEvents((current) => [event, ...current].slice(0, 300));
      },
      () => {
        // Keep previous events if stream drops.
      }
    );
    return () => source.close();
  }, [projectId, runId]);

  const eventSummary = useMemo(() => {
    let running = 0;
    let completed = 0;
    let failed = 0;
    let retry = 0;
    for (const event of events) {
      if (event.event === "NODE_STARTED") running += 1;
      if (event.event === "NODE_RESULT" && event.level !== "error") completed += 1;
      if (event.event === "NODE_RESULT" && event.level === "error") failed += 1;
      if (event.event === "GATE_RETRY") retry += 1;
    }
    return { running, completed, failed, retry };
  }, [events]);

  async function fetchBundle() {
    if (!runId) return;
    try {
      const result = await getBundle(projectId, runId);
      setBundle(result);
    } catch (err) {
      setError(String(err));
    }
  }

  async function requestCancel() {
    if (!runId) return;
    try {
      await cancelRun(projectId, runId);
      const fresh = await getRun(projectId, runId);
      setRun(fresh);
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <SectionCard title={`Run ${runId}`} kicker="Live">
      <div className="stats-grid">
        <article>
          <span>STATUS</span>
          <strong>{run?.status ?? "loading"}</strong>
        </article>
        <article>
          <span>RUNNING</span>
          <strong>{eventSummary.running}</strong>
        </article>
        <article>
          <span>COMPLETED</span>
          <strong>{eventSummary.completed}</strong>
        </article>
        <article>
          <span>FAILED</span>
          <strong>{eventSummary.failed}</strong>
        </article>
      </div>
      <div className="stack-inline">
        <button className="btn-outline" onClick={requestCancel}>
          Cancel Run
        </button>
        <button className="btn-ghost" onClick={fetchBundle}>
          Fetch Bundle
        </button>
      </div>
      <div className="event-stream">
        {events.length === 0 ? <p className="muted">Waiting for events...</p> : null}
        {events.map((event, index) => (
          <article key={`${event.event}-${index}`} className={`event-item level-${event.level}`}>
            <p>
              <strong>{event.event}</strong> <span>{event.message}</span>
            </p>
            <small>
              stage={event.stage} group={event.group ?? "-"} task={event.task_id ?? "-"}
            </small>
          </article>
        ))}
      </div>
      {bundle ? (
        <div>
          <p className="kicker">Final Bundle</p>
          <pre className="json-box">{JSON.stringify(bundle, null, 2)}</pre>
        </div>
      ) : null}
      {error ? <p className="error-line">{error}</p> : null}
    </SectionCard>
  );
}

function SettingsPermissionsPage({ projectId }: { projectId: string }) {
  const [policy, setPolicy] = useState<ToolPolicy | null>(null);
  const [allowTools, setAllowTools] = useState("tool:*");
  const [denyTools, setDenyTools] = useState("");
  const [allowMcps, setAllowMcps] = useState("mcp:*");
  const [denyMcps, setDenyMcps] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getToolPolicy(projectId)
      .then((result) => {
        setPolicy(result);
        setAllowTools(result.allow_tools.join(","));
        setDenyTools(result.deny_tools.join(","));
        setAllowMcps(result.allow_mcps.join(","));
        setDenyMcps(result.deny_mcps.join(","));
      })
      .catch((err) => setError(String(err)));
  }, [projectId]);

  async function save() {
    setError("");
    try {
      const result = await updateToolPolicy(projectId, {
        allow_tools: parseCsv(allowTools),
        deny_tools: parseCsv(denyTools),
        allow_mcps: parseCsv(allowMcps),
        deny_mcps: parseCsv(denyMcps),
      });
      setPolicy(result);
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <SectionCard title="Permissions" kicker="Settings">
      <p className="muted">These policies apply only to new runs. Existing runs keep immutable snapshots.</p>
      <div className="policy-grid">
        <div>
          <label>Allow Tools</label>
          <input value={allowTools} onChange={(event) => setAllowTools(event.target.value)} />
        </div>
        <div>
          <label>Deny Tools</label>
          <input value={denyTools} onChange={(event) => setDenyTools(event.target.value)} />
        </div>
        <div>
          <label>Allow MCPs</label>
          <input value={allowMcps} onChange={(event) => setAllowMcps(event.target.value)} />
        </div>
        <div>
          <label>Deny MCPs</label>
          <input value={denyMcps} onChange={(event) => setDenyMcps(event.target.value)} />
        </div>
      </div>
      <button className="btn-primary" onClick={save}>
        Save Policy
      </button>
      {policy ? <p className="muted">Updated: {new Date(policy.updated_at).toLocaleString()}</p> : null}
      {error ? <p className="error-line">{error}</p> : null}
    </SectionCard>
  );
}

function SettingsRunnerPage({ projectId }: { projectId: string }) {
  const [capabilities, setCapabilities] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getCapabilities(projectId)
      .then(setCapabilities)
      .catch((err) => setError(String(err)));
  }, [projectId]);

  return (
    <SectionCard title="Runner Capabilities" kicker="Settings">
      <button className="btn-ghost" onClick={() => getCapabilities(projectId).then(setCapabilities).catch((err) => setError(String(err)))}>
        Refresh
      </button>
      {capabilities ? <pre className="json-box">{JSON.stringify(capabilities, null, 2)}</pre> : <p className="muted">No capability payload yet.</p>}
      {error ? <p className="error-line">{error}</p> : null}
    </SectionCard>
  );
}

function SettingsAccountPage({ email }: { email: string }) {
  return (
    <SectionCard title="Account" kicker="Settings">
      <p className="muted">Signed in as {email}</p>
    </SectionCard>
  );
}

export default function App() {
  const [token, setTokenState] = useState(getToken());
  const [bootstrap, setBootstrap] = useState<BootstrapPayload | null>(null);
  const [projectId, setProjectId] = useState("");
  const [plans, setPlans] = useState<PlanFile[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState(localStorage.getItem("ralphite.selected_plan_id") ?? "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function refreshBootstrapAndPlans() {
    setLoading(true);
    setError("");
    try {
      await ensureDefaultProject();
      const result = await getBootstrap();
      setBootstrap(result);
      setProjectId(result.default_project_id);
      const discovered = await getPlans(result.default_project_id);
      setPlans(discovered);
      if (!selectedPlanId && discovered.length > 0) {
        const fallback = discovered[0].id;
        setSelectedPlanId(fallback);
        localStorage.setItem("ralphite.selected_plan_id", fallback);
      }
    } catch (err) {
      setError(String(err));
      setToken(null);
      setTokenState(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    void refreshBootstrapAndPlans();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function refreshPlans() {
    if (!projectId) return;
    const discovered = await getPlans(projectId);
    setPlans(discovered);
  }

  function persistSelectedPlan(planId: string) {
    setSelectedPlanId(planId);
    localStorage.setItem("ralphite.selected_plan_id", planId);
  }

  async function onAuthenticated() {
    setTokenState(getToken());
    await refreshBootstrapAndPlans();
  }

  function logout() {
    setToken(null);
    setTokenState(null);
    setBootstrap(null);
    setProjectId("");
    setPlans([]);
    setSelectedPlanId("");
    localStorage.removeItem("ralphite.selected_plan_id");
  }

  if (!token) {
    return <AuthScreen onAuthenticated={onAuthenticated} />;
  }

  if (loading && !bootstrap) {
    return (
      <main className="app-shell">
        <p className="muted">Loading workspace...</p>
      </main>
    );
  }

  if (!bootstrap || !projectId) {
    return (
      <main className="app-shell">
        <p className="error-line">{error || "Failed to load bootstrap state."}</p>
        <button className="btn-outline" onClick={logout}>
          Log out
        </button>
      </main>
    );
  }

  const workspaceConnected = bootstrap.workspace_status.status === "connected";

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="kicker">SIGNED IN AS {bootstrap.user.email}</p>
          <h1>Ralphite V1</h1>
        </div>
        <div className="stack-inline">
          <Link className="btn-ghost" to="/onboarding/workspace">
            Workspace
          </Link>
          <Link className="btn-ghost" to="/onboarding/structure">
            Structure
          </Link>
          <Link className="btn-ghost" to="/run">
            Run
          </Link>
          <Link className="btn-ghost" to="/settings/permissions">
            Settings
          </Link>
          <button className="btn-outline" onClick={logout}>
            Log out
          </button>
        </div>
      </header>

      <Routes>
        <Route
          path="/"
          element={
            workspaceConnected ? (
              selectedPlanId ? (
                <Navigate to="/run" replace />
              ) : (
                <Navigate to="/onboarding/structure" replace />
              )
            ) : (
              <Navigate to="/onboarding/workspace" replace />
            )
          }
        />
        <Route
          path="/onboarding/workspace"
          element={<WorkspacePage bootstrap={bootstrap} projectId={projectId} onConnected={refreshBootstrapAndPlans} />}
        />
        <Route
          path="/onboarding/structure"
          element={
            workspaceConnected ? (
              <StructurePage
                projectId={projectId}
                plans={plans}
                selectedPlanId={selectedPlanId}
                setSelectedPlanId={persistSelectedPlan}
                refreshPlans={refreshPlans}
              />
            ) : (
              <Navigate to="/onboarding/workspace" replace />
            )
          }
        />
        <Route
          path="/onboarding/structure/builder"
          element={
            workspaceConnected ? (
              <BuilderPage
                projectId={projectId}
                onPlanSaved={async (plan) => {
                  await refreshPlans();
                  persistSelectedPlan(plan.id);
                }}
              />
            ) : (
              <Navigate to="/onboarding/workspace" replace />
            )
          }
        />
        <Route
          path="/onboarding/structure/builder/:planId"
          element={
            workspaceConnected ? (
              <BuilderPage
                projectId={projectId}
                onPlanSaved={async (plan) => {
                  await refreshPlans();
                  persistSelectedPlan(plan.id);
                }}
              />
            ) : (
              <Navigate to="/onboarding/workspace" replace />
            )
          }
        />
        <Route
          path="/run"
          element={workspaceConnected ? <RunPage projectId={projectId} selectedPlanId={selectedPlanId} /> : <Navigate to="/onboarding/workspace" replace />}
        />
        <Route
          path="/runs/:runId"
          element={workspaceConnected ? <RunDetailPage projectId={projectId} /> : <Navigate to="/onboarding/workspace" replace />}
        />
        <Route path="/settings/permissions" element={<SettingsPermissionsPage projectId={projectId} />} />
        <Route path="/settings/runner" element={<SettingsRunnerPage projectId={projectId} />} />
        <Route path="/settings/account" element={<SettingsAccountPage email={bootstrap.user.email} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      {error ? <p className="error-line">{error}</p> : null}
    </main>
  );
}
