import { AlertTriangle, CheckCircle2, Download, Play, RefreshCw, Server } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelSetupJob,
  fetchSetupJob,
  fetchSetupStatus,
  runSetupAction,
  type FirstRunSetupStatus,
  type SetupAction,
  type SetupJob,
} from "../api";

function readinessLabel(value: boolean | null): string {
  if (value === true) return "Ready";
  if (value === false) return "Needs setup";
  return "Checking";
}

function actionIcon(actionId: string) {
  if (actionId === "start_ollama") return <Play size={15} />;
  if (actionId === "pull_model") return <Download size={15} />;
  if (actionId === "verify_model") return <RefreshCw size={15} />;
  return <Server size={15} />;
}

function ProgressLine({ job }: { job: SetupJob | null }) {
  const latest = job?.events?.[job.events.length - 1];
  const percent = typeof latest?.percent === "number" ? latest.percent : null;
  const status = typeof latest?.status === "string" ? latest.status : job?.status ?? "queued";

  return (
    <div className="setup-progress">
      <div className="setup-progress-head">
        <span>{status}</span>
        <span>{percent === null ? job?.status ?? "queued" : `${percent.toFixed(1)}%`}</span>
      </div>
      <div className="setup-progress-track">
        <div
          className="setup-progress-bar"
          style={{ width: `${Math.max(4, Math.min(100, percent ?? 8))}%` }}
        />
      </div>
      {job?.error && <div className="setup-error">{job.error}</div>}
    </div>
  );
}

function SetupActionButton({
  action,
  disabled,
  onRun,
}: {
  action: SetupAction;
  disabled: boolean;
  onRun: (action: SetupAction) => void;
}) {
  return (
    <button className="setup-action" disabled={disabled} onClick={() => onRun(action)}>
      {actionIcon(action.id)}
      <span>
        <strong>{action.label}</strong>
        <small>{action.reason}</small>
      </span>
    </button>
  );
}

export default function SetupWizard({ onContinue }: { onContinue: () => void }) {
  const [status, setStatus] = useState<FirstRunSetupStatus | null>(null);
  const [busyAction, setBusyAction] = useState<string>("");
  const [activeJob, setActiveJob] = useState<SetupJob | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    const next = await fetchSetupStatus();
    setStatus(next);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!activeJob || ["completed", "failed", "canceled"].includes(activeJob.status)) return undefined;
    const interval = window.setInterval(async () => {
      const next = await fetchSetupJob(activeJob.job_id);
      if (!next) return;
      setActiveJob(next);
      if (["completed", "failed", "canceled"].includes(next.status)) {
        window.clearInterval(interval);
        setBusyAction("");
        void refresh();
      }
    }, 1500);
    return () => window.clearInterval(interval);
  }, [activeJob, refresh]);

  const recommendedModel = status?.recommended_model?.model_id ?? "qwen3-coder-next:latest";
  const modelOptions = status?.model_options?.models ?? [];
  const activeModel = selectedModel || recommendedModel;
  const activeModelOption = modelOptions.find((model) => model.model_id === activeModel);
  const activeAssessment =
    activeModelOption?.resource_assessment ?? status?.recommended_model_resource_assessment ?? null;
  const blockingActions = useMemo(
    () => (status?.next_actions ?? []).filter((action) => action.id !== "run_mini_preflight"),
    [status?.next_actions]
  );
  const miniPreflightAction = (status?.next_actions ?? []).find(
    (action) => action.id === "run_mini_preflight"
  );

  useEffect(() => {
    if (!selectedModel && recommendedModel) {
      setSelectedModel(recommendedModel);
    }
  }, [recommendedModel, selectedModel]);

  const runAction = useCallback(
    async (action: SetupAction) => {
      setMessage("");
      if (["connect_api", "install_ollama", "free_disk_for_model"].includes(action.id)) {
        setMessage(action.reason);
        return;
      }

      setBusyAction(action.id);
      const response = await runSetupAction(action.id, activeModel);
      if (response.job) {
        setActiveJob(response.job);
        return;
      }
      if (response.detail) setMessage(response.detail);
      if (response.result) {
        const succeeded = response.result.succeeded === true;
        setMessage(succeeded ? "Action completed." : String(response.result.error ?? "Action failed."));
      }
      setBusyAction("");
      await refresh();
    },
    [activeModel, refresh]
  );

  const cancelActiveJob = useCallback(async () => {
    if (!activeJob) return;
    const next = await cancelSetupJob(activeJob.job_id);
    if (next) setActiveJob(next);
    setBusyAction("");
  }, [activeJob]);

  if (!status) {
    return (
      <div className="setup-screen">
        <div className="setup-shell">
          <RefreshCw className="spin" size={18} />
          <span>Checking Bio-Harness setup...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="setup-screen">
      <div className="setup-shell">
        <div className="setup-header">
          <div>
            <div className="setup-kicker">First-run setup</div>
            <h1>Prepare Bio-Harness for local agent runs</h1>
            <p>
              Check the environment, choose a tested local model, verify Ollama, and run the
              first tiny preflight before starting real analyses.
            </p>
          </div>
          <button className="setup-secondary" onClick={refresh} disabled={Boolean(busyAction)}>
            <RefreshCw size={15} />
            Refresh
          </button>
        </div>

        <div className="setup-grid">
          <section className="setup-panel">
            <div className="setup-panel-title">Readiness</div>
            <div className="setup-check-row">
              <CheckCircle2 size={17} />
              <span>Environment</span>
              <strong>{readinessLabel(status.environment_ready)}</strong>
            </div>
            <div className="setup-check-row">
              <CheckCircle2 size={17} />
              <span>Local model</span>
              <strong>{readinessLabel(status.model_ready)}</strong>
            </div>
            <div className="setup-check-row">
              <Server size={17} />
              <span>Recommended model</span>
              <strong>{recommendedModel}</strong>
            </div>
            {activeAssessment && (
              <div className="setup-resource-list">
                <span>Download: {activeAssessment.estimated_download_gb} GB</span>
                <span>Required free disk: {activeAssessment.required_free_disk_gb} GB</span>
                <span>Available RAM: {activeAssessment.available_ram_gb ?? "unknown"} GB</span>
              </div>
            )}
          </section>

          <section className="setup-panel">
            <div className="setup-panel-title">Recommended Models</div>
            {modelOptions.length > 0 && (
              <select
                className="setup-select"
                value={activeModel}
                onChange={(event) => setSelectedModel(event.target.value)}
              >
                {modelOptions.map((model) => (
                  <option value={model.model_id} key={model.model_id}>
                    {model.display_name} - {model.model_id}
                  </option>
                ))}
              </select>
            )}
            <div className="setup-model-list">
              {modelOptions.slice(0, 3).map((model) => (
                <div className="setup-model-row" key={model.model_id}>
                  <div>
                    <strong>{model.display_name}</strong>
                    <span>{model.model_id}</span>
                  </div>
                  <small className={model.installed ? "ok" : ""}>
                    {model.installed ? "Installed" : `${model.estimated_download_gb} GB`}
                  </small>
                </div>
              ))}
            </div>
          </section>
        </div>

        {blockingActions.length > 0 ? (
          <section className="setup-panel">
            <div className="setup-panel-title">Next Actions</div>
            <div className="setup-action-list">
              {blockingActions.map((action) => (
                <SetupActionButton
                  key={action.id}
                  action={action}
                  disabled={Boolean(busyAction)}
                  onRun={runAction}
                />
              ))}
            </div>
            {activeJob && <ProgressLine job={activeJob} />}
            {activeJob && !["completed", "failed", "canceled"].includes(activeJob.status) && (
              <button className="setup-secondary setup-cancel" onClick={cancelActiveJob}>
                Cancel job
              </button>
            )}
            {message && (
              <div className="setup-note">
                <AlertTriangle size={15} />
                <span>{message}</span>
              </div>
            )}
          </section>
        ) : (
          <section className="setup-panel setup-ready-panel">
            <CheckCircle2 size={22} />
            <div>
              <strong>Setup is ready enough to open Bio-Harness.</strong>
              <span>The remaining check is the mini preflight, which can run from the app workflow.</span>
            </div>
            {miniPreflightAction && (
              <SetupActionButton
                action={miniPreflightAction}
                disabled={Boolean(busyAction)}
                onRun={runAction}
              />
            )}
          </section>
        )}

        {blockingActions.length === 0 && activeJob && <ProgressLine job={activeJob} />}
        {blockingActions.length === 0 &&
          activeJob &&
          !["completed", "failed", "canceled"].includes(activeJob.status) && (
            <button className="setup-secondary setup-cancel" onClick={cancelActiveJob}>
              Cancel job
            </button>
          )}
        {blockingActions.length === 0 && message && (
          <div className="setup-note">
            <AlertTriangle size={15} />
            <span>{message}</span>
          </div>
        )}

        <div className="setup-footer-actions">
          <button className="setup-primary" onClick={onContinue}>
            Open Bio-Harness
          </button>
        </div>
      </div>
    </div>
  );
}
