"""Execution-loop helpers for the end-to-end harness."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from bio_harness.core.execution_monitor_support import (
    active_step_completion_evidence,
    has_live_executor_process,
    reset_execution_run_state,
    should_drain_completed_execution,
    startup_phase_grace_seconds,
    update_active_execution_context,
)
from bio_harness.core.executor_runtime import load_executor_runtime
from bio_harness.core.benchmark_asset_integrity import repair_benchmark_input_assets
from bio_harness.core.input_quality import scan_plan_inputs
from bio_harness.core.in_run_quality_monitor import update_in_run_quality_state
from bio_harness.core.preflight_summary import (
    build_preflight_summary,
    preflight_summary_to_json,
    preflight_summary_to_markdown,
)
from scripts.run_agent_e2e_execution_marker_support import (
    process_execution_marker_line,
)
from scripts.run_agent_e2e_support import (
    POST_COMPLETION_DRAIN_SECONDS,
    _ExecutionMonitorState,
    _emit,
    _is_empty_contract,
    _preflight_execution_issues,
    _reconcile_missing_sample_groups,
    _stream_evidence,
    _verify_run_outputs,
    assess_plan_contract,
    collect_process_snapshot,
    collect_recent_outputs,
    queue,
    should_mark_stalled,
    threading,
    time,
)
from bio_harness.harness.deliverable_packaging import package_deliverables


class AgentE2EExecutionMixin:
    _PRESTEP_EXECUTION_PHASES = frozenset(
        {
            "",
            "executor_preflight",
            "pre_execution_validation",
            "executor_state_init",
            "executor_dispatch",
        }
    )

    def _assess_completed_run_contract(self) -> dict[str, object]:
        """Assess post-execution contract coverage using execution-aware scoping."""

        contract = self.run.get("plan_contract", {})
        if _is_empty_contract(contract):
            return {
                "passed": True,
                "missing_capabilities": [],
                "missing_required_tool_hints": [],
                "missing_tool_hints": [],
            }
        if hasattr(self, "_assess_contract_for_plan"):
            return self._assess_contract_for_plan(
                self.run.get("plan") or {},
                contract,
            )
        return assess_plan_contract(
            self.run.get("plan") or {},
            contract,
            capability_specs=self.capability_specs,
        )

    def _active_step_completion_evidence(self, state: _ExecutionMonitorState) -> dict[str, object]:
        """Return completion evidence for the currently active step, if any."""
        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        return active_step_completion_evidence(
            state,
            plan=plan,
            selected_dir=self.cfg.selected_dir,
        )

    def _preflight(self) -> tuple[bool, str]:
        _atype = str(
            (self.run.get("analysis_spec", {}) or {}).get("analysis_type", "")
        ).strip()
        benchmark_asset_repairs = repair_benchmark_input_assets(
            data_root=self.cfg.data_root,
            analysis_type=_atype,
        )
        self.run["benchmark_asset_repairs"] = {
            "matched_profile": benchmark_asset_repairs.matched_profile,
            "changed": bool(benchmark_asset_repairs.changed),
            "actions": [asdict(action) for action in benchmark_asset_repairs.actions],
        }
        if benchmark_asset_repairs.actions:
            self._append_event(
                step_id=None,
                agent="PreflightAgent",
                event_type="BENCHMARK_ASSET_REPAIR",
                severity="info",
                payload=self.run["benchmark_asset_repairs"],
            )
        input_scan = scan_plan_inputs(
            self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
            self.cfg.data_root,
            selected_dir=self.cfg.selected_dir,
            analysis_type=_atype,
        )
        self.run["input_quality"] = {
            "has_blocking": bool(input_scan.has_blocking),
            "summary": input_scan.summary,
            "issues": [asdict(issue) for issue in input_scan.issues],
        }
        if input_scan.issues:
            self._append_event(
                step_id=None,
                agent="PreflightAgent",
                event_type="INPUT_QUALITY_SCAN",
                severity="warning" if input_scan.has_blocking else "info",
                payload=self.run["input_quality"],
            )
        self._persist_preflight_summary(analysis_type=_atype)
        preflight = _preflight_execution_issues(
            self.run.get("plan", {}),
            self.cfg.data_root,
            self.run.get("plan_contract", {}),
            self.cfg.selected_dir,
            analysis_type=_atype,
            analysis_spec=self.run.get("analysis_spec", {}),
        )
        if (
            preflight["missing_data_root"]
            or preflight["missing_fastq"]
            or preflight["missing_references"]
        ):
            msg = "Preflight blocked execution due to missing requirements."
            details = []
            if preflight["missing_data_root"]:
                details.append(f"data_root_missing={self.cfg.data_root}")
            if preflight["missing_fastq"]:
                details.append("no_fastq_discovered")
            if preflight["missing_references"]:
                details.append(f"missing_references={preflight['missing_references'][:4]}")
            detail_text = ", ".join(details)
            return False, f"{msg} ({detail_text})"
        if input_scan.has_blocking:
            blocking_categories = [
                str(issue.get("category", "")).strip()
                for issue in self.run.get("input_quality", {}).get("issues", [])
                if isinstance(issue, dict) and str(issue.get("severity", "")).strip().lower() == "error"
            ]
            detail_text = ", ".join(sorted({item for item in blocking_categories if item}))
            rendered_detail = f" ({detail_text})" if detail_text else ""
            return False, (
                "Preflight blocked execution due to blocking input-quality issues"
                f"{rendered_detail}. {input_scan.summary}"
            )
        # Missing groups is a soft warning — template compilers infer groups
        # from filename ordering when explicit labels aren't present.
        if preflight["missing_groups"]:
            self.run.setdefault("missing_sample_groups", []).extend(preflight["missing_groups"])
            print(f"[WARNING] Sample group tags not found in filenames: {preflight['missing_groups']} (template compiler will assign groups)")
        if input_scan.issues and not self.cfg.quiet:
            print(f"[WARNING] {input_scan.summary}", flush=True)
        return True, ""

    def _persist_preflight_summary(self, *, analysis_type: str) -> None:
        """Persist one reporting-safe preflight summary into the live run dir."""

        run_files = self.run.get("run_files", {})
        if not isinstance(run_files, dict):
            return
        summary_path_text = str(run_files.get("preflight_summary", "") or "").strip()
        summary_md_path_text = str(run_files.get("preflight_summary_md", "") or "").strip()
        if not summary_path_text or not summary_md_path_text:
            return

        summary = build_preflight_summary(
            self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {"plan": []},
            selected_dir=self.cfg.selected_dir,
            analysis_type=analysis_type,
            data_root=self.cfg.data_root,
            persisted_input_quality=self.run.get("input_quality", {}),
        )
        summary_json_path = Path(summary_path_text)
        summary_md_path = Path(summary_md_path_text)
        summary_json_path.write_text(
            json.dumps(preflight_summary_to_json(summary), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(
            preflight_summary_to_markdown(summary).strip() + "\n",
            encoding="utf-8",
        )

    def _update_run_markers_from_line(self, line: str) -> None:
        process_execution_marker_line(
            self.run,
            line,
            now_ts=time.time(),
            note_failure_signature=self._note_failure_signature,
        )

    def _new_execution_monitor_state(self) -> _ExecutionMonitorState:
        """Initializes execution-loop monitoring state for the current run."""
        last_progress_ts = float(self.run["last_executor_event_ts"])
        return _ExecutionMonitorState(
            last_progress_ts=last_progress_ts,
            active_step_started_ts=last_progress_ts,
            active_phase_started_ts=last_progress_ts,
        )

    def _startup_phase_grace_seconds(self, state: _ExecutionMonitorState) -> int:
        """Return a bounded grace window for pre-PID execution startup."""
        return startup_phase_grace_seconds(
            state,
            stall_timeout_seconds=int(self.cfg.stall_timeout_seconds),
            adaptive_live_process_grace_seconds=self._adaptive_live_process_grace_seconds,
            prestep_execution_phases=self._PRESTEP_EXECUTION_PHASES,
        )

    def _has_live_executor_process(self, active_pid: int | None) -> bool:
        """Returns whether the active executor process or monitored tree is still live."""
        return has_live_executor_process(
            active_pid,
            self.run.get("process_monitor_last", {}) if isinstance(self.run.get("process_monitor_last", {}), dict) else {},
        )

    def _emit_execution_heartbeat(self, state: _ExecutionMonitorState, *, now_ts: float) -> None:
        """Records process, stream, and artifact heartbeat state for a running plan."""
        idle_for = int(max(0, now_ts - state.last_progress_ts))
        monitor = collect_process_snapshot(state.active_pid, command_hint=state.active_command, max_processes=6)
        self.run["process_monitor_last"] = monitor
        tree_cpu_seconds = float(monitor.get("tree_cpu_seconds", 0.0) or 0.0)
        inferred_tool = str(monitor.get("inferred_tool", "unknown") or "unknown")
        live_count = int(monitor.get("live_process_count", 0))
        if live_count <= 0:
            inferred_tool = state.active_tool_name or inferred_tool
        stream_tier = _stream_evidence(self.run, now_ts=now_ts)
        if state.active_step_id is not None:
            artifact_tier = collect_recent_outputs(
                self.cfg.selected_dir,
                since_ts=float(state.active_step_started_ts),
                max_files=8,
                max_scan=12000,
            )
        else:
            artifact_tier = {"recent_files": [], "latest_mtime": 0.0}
        self.run["last_artifact_probe"] = artifact_tier
        in_run_quality_summary, in_run_quality_events = update_in_run_quality_state(
            self.run,
            selected_dir=self.cfg.selected_dir,
            artifact_tier=artifact_tier,
            active_step_id=state.active_step_id,
            active_tool_name=state.active_tool_name or inferred_tool,
            run_files=self.run.get("run_files", {}) if isinstance(self.run.get("run_files", {}), dict) else None,
        )
        latest_artifact_ts = float(artifact_tier.get("latest_mtime", 0.0) or 0.0)
        if state.active_step_id is not None and latest_artifact_ts > state.last_progress_ts:
            state.last_progress_ts = latest_artifact_ts
            idle_for = int(max(0, now_ts - state.last_progress_ts))
        if tree_cpu_seconds > (state.last_tree_cpu_seconds + 0.05):
            state.last_progress_ts = now_ts
            state.last_cpu_progress_ts = now_ts
            idle_for = int(max(0, now_ts - state.last_progress_ts))
        state.last_tree_cpu_seconds = tree_cpu_seconds
        cpu_progress_window = max(20, int(self.cfg.heartbeat_seconds) * 3)
        state.latest_cpu_progressing = bool(
            state.last_cpu_progress_ts > 0.0 and (now_ts - state.last_cpu_progress_ts) <= float(cpu_progress_window)
        )
        pid_text = str(state.active_pid) if isinstance(state.active_pid, int) else "-"
        artifact_count = len(artifact_tier.get("recent_files", []))
        _emit(
            (
                f"[heartbeat] run={self.run['run_uid']} status=running idle_for={idle_for}s "
                f"pid={pid_text} proc={inferred_tool} live_procs={live_count} recent_outputs={artifact_count}"
            ),
            quiet=self.cfg.quiet,
        )
        self._append_event(
            step_id=None,
            agent="Orchestrator",
            event_type="UI_HEARTBEAT",
            severity="info",
            payload={
                "status": "plan_running",
                "idle_for_seconds": idle_for,
                "pid": state.active_pid,
                "inferred_tool": inferred_tool,
                "live_process_count": live_count,
                "adaptive_live_process_grace_seconds": self._adaptive_live_process_grace_seconds(
                    active_tool_name=state.active_tool_name,
                    active_command=state.active_command,
                ),
                "cpu_progressing": state.latest_cpu_progressing,
                "stream_tier": stream_tier,
                "artifact_tier": {
                    "recent_output_count": artifact_count,
                    "latest_mtime": latest_artifact_ts,
                },
                "in_run_quality": in_run_quality_summary,
            },
        )
        self._append_event(
            step_id=state.active_step_id,
            agent="ProcessMonitor",
            event_type="PROCESS_MONITOR",
            severity="info",
            payload={
                **monitor,
                "active_step_id": state.active_step_id,
                "active_tool": state.active_tool_name,
                "stream_tier": stream_tier,
                "artifact_tier": artifact_tier,
                "in_run_quality": in_run_quality_summary,
            },
        )
        for event_payload in in_run_quality_events:
            self._append_event(
                step_id=state.active_step_id,
                agent="InRunQualityMonitor",
                event_type="IN_RUN_QUALITY_EVENT",
                severity=str(event_payload.get("severity", "info") or "info"),
                payload=event_payload,
            )
        state.last_heartbeat_print = now_ts

    def _should_drain_completed_execution(
        self,
        state: _ExecutionMonitorState,
        *,
        now_ts: float,
        has_live_process: bool,
    ) -> bool:
        """Returns whether a completed plan has drained long enough to stop polling."""
        if not should_drain_completed_execution(
            step_statuses=self.run.get("step_statuses", []),
            has_live_process=has_live_process,
            now_ts=now_ts,
            last_progress_ts=float(state.last_progress_ts),
            drain_seconds=int(POST_COMPLETION_DRAIN_SECONDS),
        ):
            return False
        self._append_event(
            step_id=None,
            agent="Orchestrator",
            event_type="EXECUTION_DRAINED",
            severity="info",
            payload={
                "reason": "all_steps_completed",
                "drain_seconds": int(max(0, now_ts - state.last_progress_ts)),
            },
        )
        return True

    def _handle_execution_stall(
        self,
        state: _ExecutionMonitorState,
        *,
        thread: threading.Thread,
        exec_stop_event: threading.Event,
        now_ts: float,
        has_live_process: bool,
    ) -> bool:
        """Marks the run failed when the executor is idle beyond the configured grace."""
        planner_phase_active = bool(self.run.get("in_planner_phase", False))
        planner_phase_started_at = float(self.run.get("planner_phase_started_at", 0.0) or 0.0)
        last_progress_ts = state.last_progress_ts
        if planner_phase_active and planner_phase_started_at > 0.0:
            last_progress_ts = max(float(last_progress_ts), planner_phase_started_at)
        startup_phase_active = bool(thread.is_alive()) and not bool(state.first_pid_observed)
        should_fail_stalled, stall_age = should_mark_stalled(
            plan_running=True,
            thread_alive=thread.is_alive(),
            queue_empty=True,
            last_progress_ts=last_progress_ts,
            now_ts=now_ts,
            timeout_seconds=self.cfg.stall_timeout_seconds,
            stall_event_emitted=bool(self.run.get("stall_event_emitted", False)),
            has_live_executor_process=has_live_process,
            live_process_grace_seconds=self._adaptive_live_process_grace_seconds(
                active_tool_name=state.active_tool_name,
                active_command=state.active_command,
            ),
            live_process_is_progressing=state.latest_cpu_progressing,
            live_process_allow_full_grace_on_idle=self._stall_monitor_uses_full_idle_grace(
                active_tool_name=state.active_tool_name,
                active_command=state.active_command,
            ),
            startup_phase_active=startup_phase_active,
            startup_phase_grace_seconds=self._startup_phase_grace_seconds(state),
            planner_phase_active=planner_phase_active,
            planner_phase_grace_seconds=int(self.run.get("planner_phase_timeout_seconds", 0) or 0),
        )
        if not should_fail_stalled:
            return False
        evidence = self._active_step_completion_evidence(state)
        if bool(evidence.get("has_evidence", False)):
            state.last_progress_ts = now_ts
            self.run["last_artifact_probe"] = evidence
            self._append_event(
                step_id=int(getattr(state, "active_step_id", 0) or 0) or None,
                agent="Orchestrator",
                event_type="STALL_SUPPRESSED_COMPLETION_EVIDENCE",
                severity="warning",
                payload={
                    "stall_seconds": int(stall_age),
                    **evidence,
                },
            )
            return False
        exec_stop_event.set()
        self.run["status"] = "failed"
        self.run["error"] = f"Execution stalled for {stall_age}s without executor progress."
        self.run["execution_stalled_detected"] = True
        self.run["stall_event_emitted"] = True
        self._append_event(
            step_id=None,
            agent="Orchestrator",
            event_type="STALL_DETECTED",
            severity="error",
            payload={
                "stall_seconds": int(stall_age),
                "status": "failed",
                "active_phase": str(state.active_phase or ""),
                "first_pid_observed": bool(state.first_pid_observed),
            },
        )
        return True

    def _handle_execution_idle_tick(
        self,
        state: _ExecutionMonitorState,
        *,
        thread: threading.Thread,
        exec_queue: queue.Queue[str | None],
        exec_stop_event: threading.Event,
    ) -> bool:
        """Processes one queue timeout tick and returns whether execution should stop."""
        now_ts = time.time()
        if (now_ts - state.last_heartbeat_print) >= max(1, int(self.cfg.heartbeat_seconds)):
            self._emit_execution_heartbeat(state, now_ts=now_ts)

        has_live_process = self._has_live_executor_process(state.active_pid)
        if self._should_drain_completed_execution(state, now_ts=now_ts, has_live_process=has_live_process):
            return True
        if self._handle_execution_stall(
            state,
            thread=thread,
            exec_stop_event=exec_stop_event,
            now_ts=now_ts,
            has_live_process=has_live_process,
        ):
            return True
        return (not thread.is_alive()) and exec_queue.empty()

    def _update_active_execution_context(
        self,
        line_text: str,
        state: _ExecutionMonitorState,
    ) -> None:
        """Updates the current step, command, and PID hints from an executor line."""
        update_active_execution_context(line_text, state)

    def _process_execution_line(self, line_text: str, state: _ExecutionMonitorState) -> None:
        """Consumes one executor line and updates run and monitor state."""
        self._append_log(line_text)
        self._update_run_markers_from_line(line_text)
        self._update_active_execution_context(line_text, state)
        state.last_progress_ts = time.time()
        if not self.cfg.quiet:
            print(line_text, end="" if line_text.endswith("\n") else "\n", flush=True)

    def _finalize_completed_run(self) -> None:
        """Apply end-of-run completion checks and deliverable packaging."""

        coverage = self._assess_completed_run_contract()
        self.run["contract_validation"] = coverage
        if not coverage.get("passed", False):
            self.run["status"] = "failed"
            self.run["error"] = (
                "Completed execution did not satisfy request contract: "
                f"missing capabilities={coverage.get('missing_capabilities', [])}, "
                f"missing required tool hints={coverage.get('missing_required_tool_hints', [])}, "
                f"missing tool hints={coverage.get('missing_tool_hints', [])}"
            )
        elif self.run.get("status") == "completed":
            self.run["error"] = ""
            self.run["stepwise_last_step_failed"] = False

        if self.run["status"] == "completed":
            packaging = package_deliverables(
                selected_dir=self.cfg.selected_dir,
                analysis_spec=self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {},
                plan=self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
                data_root=self.cfg.data_root,
                request_text=str(self.run.get("user_request", "")),
            )
            for export_meta in packaging.get("exported", []):
                self._append_event(
                    step_id=None,
                    agent="DeliverableAgent",
                    event_type="DELIVERABLE_MATERIALIZED",
                    severity="info",
                    payload=export_meta,
                )
            for failure_meta in packaging.get("failures", []):
                self._append_event(
                    step_id=None,
                    agent="DeliverableAgent",
                    event_type="DELIVERABLE_PACKAGING_FAILED",
                    severity="warning",
                    payload=failure_meta,
                )

        if self.run["status"] == "completed":
            ok_outputs, verify_msg = _verify_run_outputs(self.cfg.selected_dir, self.run.get("plan", {}))
            if not ok_outputs:
                self.run["status"] = "failed"
                self.run["error"] = verify_msg

        if self.run["status"] == "completed":
            self._augment_group_observation_from_plan_artifacts()
        _reconcile_missing_sample_groups(self.run)
        if self.run["status"] == "completed" and bool(self.run.get("no_fastq_found", False)):
            self.run["status"] = "failed"
            self.run["error"] = (
                "FASTQ discovery produced zero files for the selected data root. "
                "Run cannot be marked completed."
            )
        if self.run["status"] == "completed" and self.run.get("missing_sample_groups"):
            unresolved = list(self.run.get("missing_sample_groups", []))
            self.run["status"] = "failed"
            self.run["error"] = (
                "Run completed with unresolved sample-group evidence gaps: "
                f"{unresolved}. Refusing to mark completed."
            )

    def _execute_once(self, *, finalize_run: bool = True) -> None:
        reset_execution_run_state(self.run)

        exec_queue: queue.Queue[str | None] = queue.Queue()
        exec_stop_event = threading.Event()
        run_artifacts = dict(self.run.get("run_files", {}) if isinstance(self.run.get("run_files", {}), dict) else {})
        run_artifacts["run_id"] = self.run["run_uid"]
        run_artifacts["events"] = self.run["run_files"]["events"]
        thread = threading.Thread(
            target=self.orchestrator.execute_plan,
            args=(
                self.run["plan"],
                exec_queue,
                str(self.cfg.selected_dir),
                str(self.cfg.workspace_root),
            ),
            kwargs={
                "run_artifacts": run_artifacts,
                "stop_event": exec_stop_event,
                "current_step_idx": int(self.run.get("next_step_idx", 0) or 0),
            },
            daemon=True,
        )
        thread.start()
        self._append_event(
            step_id=None,
            agent="PlannerAgent",
            event_type="STEP_STARTED",
            severity="info",
            payload={"message": "CLI execution started"},
        )

        monitor_state = self._new_execution_monitor_state()

        while True:
            try:
                line = exec_queue.get(timeout=1.0)
            except queue.Empty:
                if self._handle_execution_idle_tick(
                    monitor_state,
                    thread=thread,
                    exec_queue=exec_queue,
                    exec_stop_event=exec_stop_event,
                ):
                    break
                continue

            if line is None:
                break

            self._process_execution_line(str(line), monitor_state)

        exec_stop_event.set()
        thread.join(timeout=5.0)
        executor_runtime = load_executor_runtime(run_artifacts)
        executor_status = str(executor_runtime.get("status", "") or "").strip().lower()
        executor_error = str(executor_runtime.get("error", "") or "").strip()
        if self.run.get("status") == "running":
            if executor_status == "failed":
                self.run["status"] = "failed"
                if executor_error:
                    self.run["error"] = executor_error
                elif not self.run.get("error"):
                    self.run["error"] = "Executor failed before step completion."
            elif any(s == "failed" for s in self.run.get("step_statuses", [])):
                self.run["status"] = "failed"
                if not self.run.get("error"):
                    self.run["error"] = "One or more steps failed."
            else:
                self.run["status"] = "completed"
                self.run["next_step_idx"] = len(self.run.get("step_statuses", []))

        self.run["stepwise_last_step_failed"] = False
        if self.run["status"] == "completed":
            if finalize_run:
                self._finalize_completed_run()
            else:
                self.run["status"] = "planned"
                self.run["next_step_idx"] = len(self.run.get("step_statuses", []))
                self.run["finished_at"] = ""
        elif not finalize_run and self.run["status"] == "failed":
            self.run["stepwise_last_step_failed"] = True
            self.run["status"] = "planned"
            self.run["finished_at"] = ""

        self._persist_state()
        self._write_exit()
