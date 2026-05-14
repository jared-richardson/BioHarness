import { Check, Play, Clock } from "lucide-react";
import type { ExecutionCard as ExecCardType } from "../types";

interface Props {
  card: ExecCardType;
}

export default function ExecutionCard({ card }: Props) {
  return (
    <div className="exec-card">
      <div className="exec-card-header">
        <span className="title">{card.title}</span>
        <span className={`status-pill ${card.status}`}>
          {card.status === "completed" ? "Completed" : "Running"}
        </span>
      </div>
      <div className="exec-progress">
        <div
          className="exec-progress-bar"
          style={{ width: `${card.progress}%` }}
        />
      </div>
      <div className="exec-steps">
        {card.steps.map((step, i) => (
          <div key={i} className={`exec-step ${step.status}`}>
            <span className="step-icon">
              {step.status === "completed" ? (
                <Check size={14} />
              ) : step.status === "running" ? (
                <Play size={14} />
              ) : (
                <Clock size={14} />
              )}
            </span>
            <span className="step-name">
              <span className="tool">{step.tool}</span> — {step.label}
            </span>
            <span className="step-time">{step.time}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
