import { X } from "lucide-react";
import { useApp } from "../context";
import type { PanelTab } from "../types";
import ArtifactsPanel from "./panels/ArtifactsPanel";
import PipelinePanel from "./panels/PipelinePanel";
import LogsPanel from "./panels/LogsPanel";
import FilesPanel from "./panels/FilesPanel";
import SettingsPanel from "./panels/SettingsPanel";
import SkillsPanel from "./panels/SkillsPanel";
import TerminalPanel from "./panels/TerminalPanel";

const TABS: { id: PanelTab; label: string }[] = [
  { id: "artifacts", label: "Artifacts" },
  { id: "pipeline", label: "Pipeline" },
  { id: "logs", label: "Logs" },
  { id: "files", label: "Files" },
  { id: "settings", label: "Settings" },
];

const PANEL_MAP: Record<PanelTab, React.FC> = {
  artifacts: ArtifactsPanel,
  pipeline: PipelinePanel,
  logs: LogsPanel,
  files: FilesPanel,
  settings: SettingsPanel,
  skills: SkillsPanel,
  terminal: TerminalPanel,
};

export default function RightPanel() {
  const { activePanel, setActivePanel } = useApp();

  const collapsed = activePanel === null;
  const ActiveComponent = activePanel ? PANEL_MAP[activePanel] : null;

  return (
    <div className={`right-panel${collapsed ? " collapsed" : ""}`}>
      {!collapsed && (
        <>
          <div className="right-panel-header">
            <div className="right-panel-tabs">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  className={`rp-tab${activePanel === t.id ? " active" : ""}`}
                  onClick={() => setActivePanel(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <button className="rp-close" onClick={() => setActivePanel(null)}>
              <X size={16} />
            </button>
          </div>
          <div className="right-panel-body">
            {ActiveComponent && <ActiveComponent />}
          </div>
        </>
      )}
    </div>
  );
}
