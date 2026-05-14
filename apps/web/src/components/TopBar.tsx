import { FileText, BarChart3, ScrollText, Settings, Moon, Sun } from "lucide-react";
import { useApp } from "../context";
import type { PanelTab } from "../types";

export default function TopBar() {
  const { activePanel, togglePanel, toggleDark, darkMode, statusInfo } = useApp();

  const buttons: { panel: PanelTab; icon: React.ReactNode; label: string; extra?: React.ReactNode }[] = [
    {
      panel: "artifacts",
      icon: <FileText size={13} />,
      label: "Artifacts",
      extra: statusInfo.outputCount > 0 ? (
        <span style={{ color: "var(--green)" }}>{statusInfo.outputCount}</span>
      ) : undefined,
    },
    { panel: "pipeline", icon: <BarChart3 size={13} />, label: "Pipeline" },
    { panel: "logs", icon: <ScrollText size={13} />, label: "Logs" },
    { panel: "settings", icon: <Settings size={13} />, label: "Settings" },
  ];

  return (
    <header className="topbar">
      <div className="topbar-title">
        Bio-Harness workspace{" "}
        <span className="dim">— local runs, artifacts, and execution context</span>
      </div>
      <div className="topbar-actions">
        {buttons.map((b) => (
          <button
            key={b.panel}
            className={`topbar-btn${activePanel === b.panel ? " active" : ""}`}
            onClick={() => togglePanel(b.panel)}
          >
            {b.icon} {b.label} {b.extra}
          </button>
        ))}
        <button
          className="theme-toggle"
          onClick={toggleDark}
          title="Toggle dark mode"
        >
          {darkMode ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}
