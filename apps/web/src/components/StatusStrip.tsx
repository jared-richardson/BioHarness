import { useApp } from "../context";

export default function StatusStrip() {
  const { statusInfo } = useApp();

  const dotClass =
    statusInfo.status === "completed"
      ? "green"
      : statusInfo.status === "running"
        ? "blue"
        : "orange";

  const statusLabel =
    statusInfo.status === "completed"
      ? "Completed"
      : statusInfo.status === "running"
        ? "Running"
        : "Idle";

  return (
    <div className="status-strip">
      <div className="status-chip">
        <div className={`dot ${dotClass}`} />
        <span className="label">Status</span>
        <span className="value">{statusLabel}</span>
      </div>
      <div className="status-chip">
        <span className="label">Steps</span>
        <span className="value">
          {statusInfo.stepsCompleted}/{statusInfo.stepsTotal}
        </span>
      </div>
      <div className="status-chip">
        <span className="label">Duration</span>
        <span className="value">{statusInfo.duration}</span>
      </div>
      <div className="status-chip">
        <span className="label">Outputs</span>
        <span className="value" style={{ color: "var(--green)" }}>
          {statusInfo.outputCount} files
        </span>
      </div>
    </div>
  );
}
