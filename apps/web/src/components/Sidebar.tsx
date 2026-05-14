import { FolderOpen, Settings, Wrench, Terminal, Plus } from "lucide-react";
import { useApp } from "../context";
import type { PanelTab } from "../types";

export default function Sidebar() {
  const { runs, activeRunId, setActiveRunId, togglePanel, modelStatus, newSession, backendOnline, ollamaOnline } = useApp();

  const quickActions: { icon: React.ReactNode; label: string; panel: PanelTab; badge?: string }[] = [
    { icon: <FolderOpen size={15} />, label: "Files & Data", panel: "files" },
    { icon: <Settings size={15} />, label: "Settings", panel: "settings" },
    { icon: <Wrench size={15} />, label: "Skills & Tools", panel: "skills" },
    { icon: <Terminal size={15} />, label: "Terminal", panel: "terminal" },
  ];

  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">B</div>
        <div className="sidebar-brand">
          Bio-Harness <span>v2</span>
        </div>
      </div>

      <button className="new-chat-btn" onClick={newSession}>
        <Plus size={14} /> New Analysis
      </button>

      <div className="sidebar-section">
        <div className="sidebar-section-title">Quick Actions</div>
        {quickActions.map((a) => (
          <button
            key={a.panel}
            className="sidebar-btn"
            onClick={() => togglePanel(a.panel)}
          >
            <span className="icon">{a.icon}</span>
            {a.label}
            {a.badge && <span className="badge">{a.badge}</span>}
          </button>
        ))}
      </div>

      <div className="sidebar-section">
        <div className="sidebar-section-title">Recent Runs</div>
        {runs.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--text-dim)", padding: "4px 8px" }}>
            No runs yet
          </div>
        ) : (
          runs.map((r) => (
            <div
              key={r.id}
              className={`run-item${activeRunId === r.id ? " active" : ""}`}
              onClick={() => setActiveRunId(r.id)}
            >
              <div className={`run-dot ${r.color}`} />
              <div className="run-label">{r.label}</div>
              <div className="run-time">{r.time}</div>
            </div>
          ))
        )}
      </div>

      <div className="sidebar-footer">
        <div className="model-card">
          <div className="model-card-row">
            <div className="model-name">{modelStatus.name}</div>
            <div className="model-status">
              <div className={`dot ${ollamaOnline ? "green" : ""}`} />
              {ollamaOnline ? "Ready" : "Offline"}
            </div>
          </div>
          <div className="model-stats">
            <div className="model-stat">
              Fast: <span>{modelStatus.fastModel}</span>
            </div>
            <div className="model-stat">
              RAM: <span>{modelStatus.ramPercent}%</span>
            </div>
          </div>
          {!backendOnline && (
            <div style={{ fontSize: 10, color: "var(--red)", marginTop: 4 }}>
              API server not connected
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
