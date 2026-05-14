import { createContext, useContext, useState, useCallback, useEffect, useRef, type ReactNode } from "react";
import type { Message, RunItem, PanelTab, SettingsState, ModelStatus, StatusInfo } from "./types";
import { initTheme, toggleDarkMode, isDarkMode } from "./theme";
import {
  fetchHealth,
  fetchModels,
  fetchSystemInfo,
  sendChat,
  startRun,
  connectRunWebSocket,
  type ChatResponse,
  type RunEvent,
  type ModelInfo,
  type SystemInfo,
} from "./api";

interface AppState {
  activePanel: PanelTab | null;
  darkMode: boolean;
  messages: Message[];
  activeRunId: string | null;
  runs: RunItem[];
  settings: SettingsState;
  modelStatus: ModelStatus;
  statusInfo: StatusInfo;
  systemInfo: SystemInfo | null;
  backendOnline: boolean;
  ollamaOnline: boolean;
  availableModels: ModelInfo[];
  sending: boolean;
  setActivePanel: (panel: PanelTab | null) => void;
  togglePanel: (panel: PanelTab) => void;
  toggleDark: () => void;
  setMessages: (msgs: Message[]) => void;
  addMessage: (msg: Message) => void;
  setActiveRunId: (id: string | null) => void;
  setSettings: (s: SettingsState) => void;
  newSession: () => void;
  pendingPrompt: string | null;
  setPendingPrompt: (p: string | null) => void;
  handleSend: (text: string) => void;
}

const AppContext = createContext<AppState | null>(null);
const DEFAULT_LOCAL_MODEL = "qwen3-coder-next:latest";

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be inside AppProvider");
  return ctx;
}

const DEFAULT_SETTINGS: SettingsState = {
  llmBackend: "Ollama (Local)",
  heavyModel: DEFAULT_LOCAL_MODEL,
  fastModel: DEFAULT_LOCAL_MODEL,
  autoExecute: true,
  autoRemediate: true,
  testSubset: false,
  liveRefresh: true,
  policyMode: "scientific_harness (default)",
};

export function AppProvider({ children }: { children: ReactNode }) {
  const [activePanel, setActivePanel] = useState<PanelTab | null>("artifacts");
  const [darkMode, setDarkMode] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [settings, setSettings] = useState<SettingsState>(DEFAULT_SETTINGS);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState(false);
  const [ollamaOnline, setOllamaOnline] = useState(false);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [sending, setSending] = useState(false);
  const [statusInfo, setStatusInfo] = useState<StatusInfo>({
    status: "idle",
    stepsCompleted: 0,
    stepsTotal: 0,
    duration: "—",
    outputCount: 0,
  });
  const [modelStatus, setModelStatus] = useState<ModelStatus>({
    name: "Checking...",
    ready: false,
    fastModel: "—",
    ramPercent: 0,
  });

  const sessionIdRef = useRef(`session_${Date.now()}`);
  const wsRef = useRef<WebSocket | null>(null);

  // ------- Init theme -------
  useEffect(() => {
    initTheme();
    setDarkMode(isDarkMode());
  }, []);

  // ------- Probe backend on mount + periodic refresh -------
  useEffect(() => {
    let cancelled = false;

    async function probe() {
      const health = await fetchHealth();
      if (cancelled) return;
      setBackendOnline(health.status === "ok");
      setOllamaOnline(health.ollama);

      if (health.status === "ok") {
        const modelsResp = await fetchModels();
        if (!cancelled) {
          setAvailableModels(modelsResp.models);
          if (modelsResp.models.length > 0) {
            const preferred = (
              modelsResp.models.find((m) => m.name === DEFAULT_LOCAL_MODEL)
              || modelsResp.models.find((m) => m.name.includes("qwen3-coder"))
              || modelsResp.models.find((m) => m.name.includes("coder"))
              || modelsResp.models[0]
            );
            setModelStatus({
              name: preferred.name,
              ready: true,
              fastModel: preferred.name,
              ramPercent: 0, // updated by system info
            });
            // Update settings with detected models
            setSettings((prev) => ({
              ...prev,
              heavyModel: preferred.name,
              fastModel: preferred.name,
            }));
          }
        }

        const sys = await fetchSystemInfo();
        if (!cancelled && sys) {
          setSystemInfo(sys);
          setModelStatus((prev) => ({ ...prev, ramPercent: Math.round(sys.ram_percent) }));
        }
      }
    }

    probe();
    const interval = setInterval(probe, 15000); // refresh every 15s
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // ------- Toggle dark mode -------
  const toggleDark = useCallback(() => {
    const newDark = toggleDarkMode();
    setDarkMode(newDark);
  }, []);

  const togglePanelCb = useCallback(
    (panel: PanelTab) => {
      setActivePanel((prev) => (prev === panel ? null : panel));
    },
    []
  );

  const addMessage = useCallback((msg: Message) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const newSession = useCallback(() => {
    setMessages([]);
    setActiveRunId(null);
    setActivePanel(null);
    setStatusInfo({ status: "idle", stepsCompleted: 0, stepsTotal: 0, duration: "—", outputCount: 0 });
    sessionIdRef.current = `session_${Date.now()}`;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  // ------- Handle WebSocket run events -------
  const handleRunEvent = useCallback((event: RunEvent, assistantMsgId: string) => {
    if (event.type === "step_started") {
      setStatusInfo((prev) => ({ ...prev, status: "running" }));
      // Update the execution card step to running
      setMessages((prev) =>
        prev.map((msg) => {
          if (msg.id !== assistantMsgId || !msg.execution) return msg;
          const steps = msg.execution.steps.map((s, i) =>
            i === (event.step_number || 1) - 1 ? { ...s, status: "running" as const } : s
          );
          return { ...msg, execution: { ...msg.execution, steps } };
        })
      );
    } else if (event.type === "step_completed") {
      const stepNum = event.step_number || 0;
      setStatusInfo((prev) => ({
        ...prev,
        stepsCompleted: stepNum,
      }));
      // Update the execution card step to completed
      setMessages((prev) =>
        prev.map((msg) => {
          if (msg.id !== assistantMsgId || !msg.execution) return msg;
          const steps = msg.execution.steps.map((s, i) =>
            i === stepNum - 1
              ? { ...s, status: "completed" as const, time: event.duration_s ? `${event.duration_s.toFixed(1)}s` : "—" }
              : s
          );
          const progress = Math.round((stepNum / msg.execution.steps.length) * 100);
          return { ...msg, execution: { ...msg.execution, steps, progress } };
        })
      );
    } else if (event.type === "run_completed") {
      setStatusInfo((prev) => ({
        ...prev,
        status: "completed",
        stepsCompleted: prev.stepsTotal,
      }));
      setMessages((prev) =>
        prev.map((msg) => {
          if (msg.id !== assistantMsgId || !msg.execution) return msg;
          return { ...msg, execution: { ...msg.execution, status: "completed", progress: 100 } };
        })
      );
      // Update run color in sidebar
      setRuns((prev) =>
        prev.map((r) =>
          r.id === event.run_id ? { ...r, color: "green" as const } : r
        )
      );
    } else if (event.type === "run_failed") {
      setStatusInfo((prev) => ({ ...prev, status: "completed" }));
      setRuns((prev) =>
        prev.map((r) =>
          r.id === event.run_id ? { ...r, color: "red" as const } : r
        )
      );
    }
  }, []);

  // ------- Send message to backend -------
  const handleSend = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      // Add user message
      const userMsgId = `m-${Date.now()}`;
      const userMsg: Message = { id: userMsgId, role: "user", text: trimmed };
      setMessages((prev) => [...prev, userMsg]);
      setSending(true);

      try {
        const resp: ChatResponse = await sendChat(trimmed, sessionIdRef.current, settings.fastModel);

        // Build assistant message
        // If the LLM returned a parseable plan, show the reasoning instead of raw JSON
        let displayText = resp.response;
        if (resp.plan?.steps && resp.plan.steps.length > 0) {
          displayText = resp.plan.reasoning
            || `I'll run a ${resp.plan.analysis_type?.replace(/_/g, " ") || "bioinformatics"} analysis. Here's the plan:`;
        }

        const assistantMsg: Message = {
          id: `m-${Date.now()}`,
          role: "assistant",
          text: displayText,
        };

        // If there's a plan, add an execution card
        if (resp.plan?.steps && resp.plan.steps.length > 0) {
          assistantMsg.execution = {
            title: `Execution Plan — ${resp.plan.analysis_type || "Analysis"}`,
            status: "completed",
            progress: 0,
            steps: resp.plan.steps.map((s) => ({
              tool: s.tool_name,
              label: s.description,
              time: "—",
              status: "pending" as const,
            })),
          };

          // Auto-execute if enabled
          if (settings.autoExecute && !resp.error) {
            assistantMsg.execution.status = "running";
            // Start the run
            const runResult = await startRun(resp.plan as Record<string, unknown>, "");
            if (runResult.run_id) {
              const newRun: RunItem = {
                id: runResult.run_id,
                label: resp.plan.analysis_type || "Analysis run",
                time: "just now",
                color: "blue",
              };
              setRuns((prev) => [newRun, ...prev]);
              setActiveRunId(runResult.run_id);
              setStatusInfo({
                status: "running",
                stepsCompleted: 0,
                stepsTotal: resp.plan.steps.length,
                duration: "0s",
                outputCount: 0,
              });

              // Connect WebSocket for live updates
              const ws = connectRunWebSocket(
                runResult.run_id,
                (event: RunEvent) => {
                  handleRunEvent(event, assistantMsg.id);
                },
                () => {
                  // On close — ensure status reflects completion
                }
              );
              wsRef.current = ws;
            }
          }
        }

        setMessages((prev) => [...prev, assistantMsg]);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            id: `m-${Date.now()}`,
            role: "assistant",
            text: "An error occurred while communicating with the backend.",
          },
        ]);
      } finally {
        setSending(false);
      }
    },
    [sending, settings.fastModel, settings.autoExecute, handleRunEvent]
  );

  return (
    <AppContext.Provider
      value={{
        activePanel,
        darkMode,
        messages,
        activeRunId,
        runs,
        settings,
        modelStatus,
        statusInfo,
        systemInfo,
        backendOnline,
        ollamaOnline,
        availableModels,
        sending,
        setActivePanel,
        togglePanel: togglePanelCb,
        toggleDark,
        setMessages,
        addMessage,
        setActiveRunId,
        setSettings,
        newSession,
        pendingPrompt,
        setPendingPrompt,
        handleSend,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}
