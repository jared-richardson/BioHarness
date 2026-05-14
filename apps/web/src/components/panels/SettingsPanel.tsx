import { useApp } from "../../context";

function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button className={`toggle${on ? " on" : ""}`} onClick={onToggle}>
      <div className="knob" />
    </button>
  );
}

export default function SettingsPanel() {
  const { settings, setSettings, systemInfo, backendOnline, ollamaOnline, availableModels } = useApp();

  const update = (patch: Partial<typeof settings>) =>
    setSettings({ ...settings, ...patch });

  return (
    <>
      <div className="panel-section">
        <div className="panel-section-title">Model Configuration</div>
        <div className="panel-field">
          <div className="panel-label">LLM Backend</div>
          <select
            className="panel-select"
            value={settings.llmBackend}
            onChange={(e) => update({ llmBackend: e.target.value })}
          >
            <option>Ollama (Local)</option>
            <option>vLLM</option>
            <option>MLX</option>
            <option>OpenAI-compatible</option>
          </select>
        </div>
        <div className="panel-field">
          <div className="panel-label">Heavy Model (Planning)</div>
          {availableModels.length > 0 ? (
            <select
              className="panel-select"
              value={settings.heavyModel}
              onChange={(e) => update({ heavyModel: e.target.value })}
            >
              {availableModels.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name} {m.parameter_size ? `(${m.parameter_size})` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="panel-input"
              value={settings.heavyModel}
              onChange={(e) => update({ heavyModel: e.target.value })}
            />
          )}
        </div>
        <div className="panel-field">
          <div className="panel-label">Fast Model (Execution)</div>
          {availableModels.length > 0 ? (
            <select
              className="panel-select"
              value={settings.fastModel}
              onChange={(e) => update({ fastModel: e.target.value })}
            >
              {availableModels.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name} {m.parameter_size ? `(${m.parameter_size})` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="panel-input"
              value={settings.fastModel}
              onChange={(e) => update({ fastModel: e.target.value })}
            />
          )}
        </div>
      </div>
      <div className="panel-section">
        <div className="panel-section-title">Execution Preferences</div>
        <div className="toggle-row">
          <span className="toggle-label">Auto-execute on "run" / "proceed"</span>
          <Toggle on={settings.autoExecute} onToggle={() => update({ autoExecute: !settings.autoExecute })} />
        </div>
        <div className="toggle-row">
          <span className="toggle-label">Auto-remediate missing tools</span>
          <Toggle on={settings.autoRemediate} onToggle={() => update({ autoRemediate: !settings.autoRemediate })} />
        </div>
        <div className="toggle-row">
          <span className="toggle-label">Use test subset (1M reads)</span>
          <Toggle on={settings.testSubset} onToggle={() => update({ testSubset: !settings.testSubset })} />
        </div>
        <div className="toggle-row">
          <span className="toggle-label">Live refresh during execution</span>
          <Toggle on={settings.liveRefresh} onToggle={() => update({ liveRefresh: !settings.liveRefresh })} />
        </div>
      </div>
      <div className="panel-section">
        <div className="panel-section-title">Policy Mode</div>
        <select
          className="panel-select"
          value={settings.policyMode}
          onChange={(e) => update({ policyMode: e.target.value })}
        >
          <option>scientific_harness (default)</option>
          <option>bioagentbench_planning_strict</option>
          <option>official_bioagentbench</option>
        </select>
      </div>
      <div className="panel-section">
        <div className="panel-section-title">System Resources</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.8 }}>
          {systemInfo ? (
            <>
              CPU: {systemInfo.cpu_count} cores ({systemInfo.cpu_percent}% usage)
              <br />
              RAM: {systemInfo.ram_total_gb} GB ({systemInfo.ram_percent}% used — {systemInfo.ram_used_gb} GB)
              <br />
              Disk: {systemInfo.disk_free_gb} GB free
              <br />
            </>
          ) : (
            <>System info unavailable<br /></>
          )}
          Backend:{" "}
          <span style={{ color: backendOnline ? "var(--green)" : "var(--red)" }}>
            {backendOnline ? "Connected" : "Offline"}
          </span>
          <br />
          Ollama:{" "}
          <span style={{ color: ollamaOnline ? "var(--green)" : "var(--red)" }}>
            {ollamaOnline ? "Running" : "Not connected"}
          </span>
          {availableModels.length > 0 && (
            <>
              <br />
              Models: {availableModels.length} available
            </>
          )}
        </div>
      </div>
    </>
  );
}
