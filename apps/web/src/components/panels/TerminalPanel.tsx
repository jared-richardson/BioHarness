import { useState, useRef, useEffect } from "react";
import { executeCommand } from "../../api";

interface TermLine {
  type: "prompt" | "output" | "error";
  text: string;
}

export default function TerminalPanel() {
  const [command, setCommand] = useState("");
  const [lines, setLines] = useState<TermLine[]>([
    { type: "output", text: "Bio-Harness Terminal — connected to project workspace" },
  ]);
  const [running, setRunning] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [lines]);

  const runCommand = async () => {
    const cmd = command.trim();
    if (!cmd || running) return;

    setLines((prev) => [...prev, { type: "prompt", text: `$ ${cmd}` }]);
    setCommand("");
    setRunning(true);

    try {
      const result = await executeCommand(cmd);
      const newLines: TermLine[] = [];
      if (result.stdout) {
        newLines.push({ type: "output", text: result.stdout });
      }
      if (result.stderr) {
        newLines.push({ type: "error", text: result.stderr });
      }
      if (newLines.length === 0 && result.exit_code === 0) {
        newLines.push({ type: "output", text: "(no output)" });
      }
      setLines((prev) => [...prev, ...newLines]);
    } catch {
      setLines((prev) => [...prev, { type: "error", text: "Failed to execute command" }]);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="panel-section">
      <div className="panel-section-title">Shell</div>
      <div className="panel-field">
        <div className="chat-input-row">
          <input
            className="panel-input"
            placeholder="ls workspace/outputs/"
            style={{ fontFamily: "var(--mono)", fontSize: 12 }}
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") runCommand();
            }}
            disabled={running}
          />
          <button
            className="topbar-btn"
            style={{ flexShrink: 0 }}
            onClick={runCommand}
            disabled={running}
          >
            {running ? "..." : "Run"}
          </button>
        </div>
      </div>
      <div className="terminal" style={{ marginTop: 8 }} ref={scrollRef}>
        {lines.map((line, i) => (
          <div key={i} className={line.type === "output" ? "output" : line.type === "error" ? "error" : ""}>
            {line.type === "prompt" ? (
              <span className="prompt">{line.text}</span>
            ) : (
              <span style={line.type === "error" ? { color: "#e06c75" } : undefined}>
                {line.text}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
