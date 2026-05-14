export type PanelTab =
  | "artifacts"
  | "pipeline"
  | "logs"
  | "files"
  | "settings"
  | "skills"
  | "terminal";

export type MessageRole = "user" | "assistant";

export interface ExecStep {
  tool: string;
  label: string;
  time: string;
  detail?: string;
  status: "completed" | "running" | "pending";
}

export interface ExecutionCard {
  title: string;
  status: "completed" | "running";
  progress: number; // 0-100
  steps: ExecStep[];
}

export interface Message {
  id: string;
  role: MessageRole;
  text: string;
  execution?: ExecutionCard;
}

export interface RunItem {
  id: string;
  label: string;
  time: string;
  color: "green" | "orange" | "red" | "blue";
}

export interface ArtifactFile {
  name: string;
  size: string;
  type: "table" | "image" | "text" | "html";
  tableData?: { headers: string[]; rows: string[][] };
  description?: string;
  link?: string;
}

export interface PipelineStep {
  tool: string;
  detail: string;
  time: string;
  status: "completed" | "running" | "pending";
}

export interface AnalysisSpec {
  type: string;
  organism: string;
  design: string;
  aligner: string;
  counter: string;
  deMethod: string;
  templateCompiler: string;
}

export interface LogEntry {
  type: "prompt" | "output";
  text: string;
}

export interface FileTreeItem {
  name: string;
  depth: number;
  icon: "folder" | "file" | "image" | "more";
  moreCount?: number;
}

export interface SkillItem {
  name: string;
  description: string;
}

export interface SettingsState {
  llmBackend: string;
  heavyModel: string;
  fastModel: string;
  autoExecute: boolean;
  autoRemediate: boolean;
  testSubset: boolean;
  liveRefresh: boolean;
  policyMode: string;
}

export interface SystemInfo {
  cpuCores: number;
  cpuUsage: number;
  ramTotal: number;
  ramUsedPercent: number;
  ramUsedGb: number;
  diskFreeGb: number;
  diskTotalGb: number;
  ollamaStatus: string;
  ollamaPid: number;
}

export interface ModelStatus {
  name: string;
  ready: boolean;
  fastModel: string;
  ramPercent: number;
}

export interface StatusInfo {
  status: "completed" | "running" | "idle";
  stepsCompleted: number;
  stepsTotal: number;
  duration: string;
  outputCount: number;
}
