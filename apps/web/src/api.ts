const RAW_API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
export const API_BASE = RAW_API_BASE.replace(/\/$/, "");

// ---------------------------------------------------------------------------
// Generic helpers
// ---------------------------------------------------------------------------

async function fetchJson<T>(path: string, fallback: T, timeout = 5000): Promise<T> {
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: AbortSignal.timeout(timeout) });
    if (!res.ok) throw new Error(res.statusText);
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export function workspaceFileUrl(filePath: string): string {
  return `${API_BASE}/api/workspace/file?path=${encodeURIComponent(filePath)}`;
}

function webSocketBase(): string {
  const url = new URL(API_BASE, window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString().replace(/\/$/, "");
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export interface HealthStatus {
  status: string;
  ollama: boolean;
}

export async function fetchHealth(): Promise<HealthStatus> {
  return fetchJson("/api/health", { status: "offline", ollama: false });
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

export interface ModelInfo {
  name: string;
  size: number;
  modified_at: string;
  parameter_size: string;
  family: string;
}

export async function fetchModels(): Promise<{ models: ModelInfo[]; error?: string }> {
  return fetchJson("/api/models", { models: [], error: "Backend not reachable" });
}

// ---------------------------------------------------------------------------
// First-run setup
// ---------------------------------------------------------------------------

export interface SetupAction {
  id: string;
  label: string;
  reason: string;
}

export interface SetupRecommendation {
  model_id: string;
  installed: boolean;
  reason: string;
}

export interface SetupResourceAssessment {
  model_id: string;
  installed: boolean;
  free_disk_gb: number | null;
  available_ram_gb: number | null;
  required_free_disk_gb: number;
  estimated_download_gb: number;
  min_ram_gb: number;
  recommended_ram_gb: number;
  disk_ok: boolean;
  ram_ok: boolean;
  ram_warning: boolean;
  can_pull: boolean;
}

export interface SetupModelOption {
  model_id: string;
  display_name: string;
  release_role: string;
  tested_status: string;
  estimated_download_gb: number;
  estimated_disk_required_gb: number;
  min_ram_gb: number;
  recommended_ram_gb: number;
  installed: boolean;
  recommended: boolean;
  notes: string;
  resource_assessment?: SetupResourceAssessment;
}

export interface FirstRunSetupStatus {
  schema_version: number;
  setup_complete: boolean;
  environment_ready: boolean | null;
  model_ready: boolean | null;
  recommended_model: SetupRecommendation;
  recommended_model_resource_assessment: SetupResourceAssessment | null;
  model_options: {
    models: SetupModelOption[];
    installed_model_names: string[];
  };
  next_actions: SetupAction[];
  resources?: {
    cpu_count?: number | null;
    ram_total_gb?: number | null;
    available_ram_gb?: number | null;
    disk_free_gb?: number | null;
  };
}

export interface SetupJob {
  job_id: string;
  action_id: string;
  model_name: string;
  host: string;
  status: "queued" | "running" | "cancel_requested" | "completed" | "failed" | "canceled";
  cancel_requested?: boolean;
  pid?: number | null;
  created_at: string;
  updated_at: string;
  events: Record<string, unknown>[];
  result: Record<string, unknown> | null;
  error: string;
}

const SETUP_STATUS_FALLBACK: FirstRunSetupStatus = {
  schema_version: 1,
  setup_complete: false,
  environment_ready: null,
  model_ready: null,
  recommended_model: {
    model_id: "qwen3-coder-next:latest",
    installed: false,
    reason: "Recommended public default.",
  },
  recommended_model_resource_assessment: null,
  model_options: { models: [], installed_model_names: [] },
  next_actions: [
    {
      id: "connect_api",
      label: "Connect API",
      reason: "The setup status endpoint is not reachable.",
    },
  ],
};

export async function fetchSetupStatus(): Promise<FirstRunSetupStatus> {
  return fetchJson("/api/setup/status", SETUP_STATUS_FALLBACK, 15000);
}

export async function runSetupAction(
  actionId: string,
  modelName?: string,
  host?: string
): Promise<{ action_id: string; result?: Record<string, unknown>; job?: SetupJob; detail?: string }> {
  try {
    const res = await fetch(`${API_BASE}/api/setup/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: actionId, model_name: modelName ?? "", host: host ?? "" }),
      signal: AbortSignal.timeout(15000),
    });
    const data = (await res.json()) as {
      action_id: string;
      result?: Record<string, unknown>;
      job?: SetupJob;
      detail?: string;
    };
    if (!res.ok) return { ...data, action_id: actionId };
    return data;
  } catch {
    return { action_id: actionId, detail: "Bio-Harness API is not reachable." };
  }
}

export async function fetchSetupJob(jobId: string): Promise<SetupJob | null> {
  return fetchJson(`/api/setup/jobs/${encodeURIComponent(jobId)}`, null, 10000);
}

export async function cancelSetupJob(jobId: string): Promise<SetupJob | null> {
  try {
    const res = await fetch(`${API_BASE}/api/setup/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    return (await res.json()) as SetupJob;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export interface SkillInfo {
  name: string;
  description: string;
  category?: string;
  exec_hint?: string[];
}

export async function fetchSkills(): Promise<{ skills: SkillInfo[] }> {
  const data = await fetchJson<Record<string, unknown>>("/api/skills", {});
  // The skills index has skill names as keys with objects as values
  if (data && typeof data === "object" && !Array.isArray(data)) {
    // If the API returns { skills: [...] } directly
    if ("skills" in data && Array.isArray(data.skills)) {
      return { skills: data.skills as SkillInfo[] };
    }
    // If it returns the raw index.json format (object with skill_name keys)
    const skills: SkillInfo[] = [];
    for (const [key, val] of Object.entries(data)) {
      if (typeof val === "object" && val !== null) {
        const v = val as Record<string, unknown>;
        skills.push({
          name: (v.skill_name as string) || key,
          description: (v.description as string) || "",
          category: (v.category as string) || "",
          exec_hint: (v.exec_hint as string[]) || [],
        });
      }
    }
    if (skills.length > 0) return { skills };
  }
  return { skills: [] };
}

// ---------------------------------------------------------------------------
// Workspace directories
// ---------------------------------------------------------------------------

export async function fetchDirs(): Promise<string[]> {
  const data = await fetchJson<{ dirs: { name: string; path: string }[] }>("/api/workspace/dirs", { dirs: [] });
  return data.dirs.map((d) => d.path);
}

// ---------------------------------------------------------------------------
// Workspace file tree
// ---------------------------------------------------------------------------

export interface TreeEntry {
  name: string;
  type: "dir" | "file";
  path: string;
  size: number;
}

export async function fetchTree(path: string): Promise<TreeEntry[]> {
  return fetchJson(`/api/workspace/tree?path=${encodeURIComponent(path)}`, []);
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

export interface ArtifactInfo {
  name: string;
  path: string;
  size: number;
  sizeFormatted: string;
  kind: string;
  modified: string;
}

export async function fetchArtifacts(dirPath: string): Promise<ArtifactInfo[]> {
  const raw = await fetchJson<{ name: string; path: string; size: number; kind: string; modified: string }[]>(
    `/api/workspace/artifacts?path=${encodeURIComponent(dirPath)}`,
    []
  );
  return raw.map((a) => ({
    ...a,
    sizeFormatted: formatBytes(a.size),
  }));
}

// ---------------------------------------------------------------------------
// File content
// ---------------------------------------------------------------------------

export interface TableFile {
  columns: string[];
  rows: string[][];
  total_rows: number;
  truncated?: boolean;
}

export interface TextFile {
  content_type: "text" | "json";
  content?: string;
  data?: unknown;
  size?: number;
}

export type FileContent = TableFile | TextFile;

export async function fetchFile(filePath: string): Promise<FileContent | null> {
  try {
    const res = await fetch(workspaceFileUrl(filePath), {
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    const contentType = res.headers.get("content-type") || "";
    if (contentType.startsWith("image/") || contentType === "application/pdf") {
      // Binary file served directly — return URL for <img> rendering
      return { content_type: "text", content: workspaceFileUrl(filePath) };
    }
    return (await res.json()) as FileContent;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// System info
// ---------------------------------------------------------------------------

export interface SystemInfo {
  cpu_count: number;
  cpu_percent: number;
  ram_total_gb: number;
  ram_used_gb: number;
  ram_percent: number;
  disk_free_gb: number;
}

export async function fetchSystemInfo(): Promise<SystemInfo | null> {
  const data = await fetchJson<SystemInfo | null>("/api/system", null);
  return data;
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export interface ChatResponse {
  response: string;
  plan: {
    analysis_type?: string;
    reasoning?: string;
    steps?: {
      step_number: number;
      tool_name: string;
      description: string;
      parameters?: Record<string, unknown>;
    }[];
  } | null;
  mock?: boolean;
  model?: string;
  error?: boolean;
}

export async function sendChat(
  message: string,
  sessionId: string,
  model?: string
): Promise<ChatResponse> {
  try {
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId, model }),
      signal: AbortSignal.timeout(300000), // 5 min for LLM
    });
    if (!res.ok) throw new Error(res.statusText);
    return (await res.json()) as ChatResponse;
  } catch {
    return {
      response: `Backend is not available. Please ensure the API server is running at ${API_BASE}.`,
      plan: null,
      error: true,
    };
  }
}

// ---------------------------------------------------------------------------
// Run management
// ---------------------------------------------------------------------------

export interface RunState {
  run_id: string;
  status: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  plan?: Record<string, unknown>;
  output_dir?: string;
  steps_completed: number;
  steps_total: number;
  current_step?: string | null;
  error?: string | null;
}

export async function startRun(
  plan: Record<string, unknown>,
  outputDir: string
): Promise<{ run_id: string; status: string; run_dir: string }> {
  try {
    const res = await fetch(`${API_BASE}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan, output_dir: outputDir }),
    });
    if (!res.ok) throw new Error(res.statusText);
    return (await res.json()) as { run_id: string; status: string; run_dir: string };
  } catch {
    return { run_id: "", status: "error", run_dir: "" };
  }
}

export async function fetchRunState(runId: string): Promise<RunState | null> {
  return fetchJson(`/api/run/${encodeURIComponent(runId)}`, null);
}

// ---------------------------------------------------------------------------
// WebSocket for live run events
// ---------------------------------------------------------------------------

export interface RunEvent {
  type: "run_started" | "step_started" | "step_completed" | "run_completed" | "run_failed";
  run_id?: string;
  step_number?: number;
  tool_name?: string;
  description?: string;
  exit_code?: number;
  duration_s?: number;
  ts?: string;
  error?: string;
}

export function connectRunWebSocket(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onClose?: () => void
): WebSocket {
  const ws = new WebSocket(`${webSocketBase()}/ws/run/${encodeURIComponent(runId)}`);
  ws.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data) as RunEvent;
      onEvent(event);
    } catch {
      // ignore malformed events
    }
  };
  ws.onclose = () => onClose?.();
  ws.onerror = () => onClose?.();
  return ws;
}

// ---------------------------------------------------------------------------
// Terminal (shell command execution)
// ---------------------------------------------------------------------------

export async function executeCommand(command: string): Promise<{ stdout: string; stderr: string; exit_code: number }> {
  try {
    const res = await fetch(`${API_BASE}/api/terminal/exec`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
      signal: AbortSignal.timeout(30000),
    });
    if (!res.ok) throw new Error(res.statusText);
    return (await res.json()) as { stdout: string; stderr: string; exit_code: number };
  } catch {
    return { stdout: "", stderr: "Backend not connected — command not executed", exit_code: 1 };
  }
}
