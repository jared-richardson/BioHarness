from __future__ import annotations

from scripts.run_agent_e2e_support import (
    Any,
    Path,
    TEMPLATE_COMPILER_TYPES,
    _emit,
    assess_protocol_grounding,
    deterministic_protocol_repair,
    is_bioagentbench_planning_strict_policy,
    _planner_worker_think,
    mp,
    os,
    queue,
    signal,
    threading,
    time,
)
from scripts.run_agent_e2e_validation_phase_support import (
    build_protocol_normalization_snapshot,
    should_attempt_protocol_normalization,
)
from bio_harness.core.hierarchical_planning import assemble_executable_plan
from bio_harness.core.planner_trace_recovery import load_hierarchical_trace_recovery_state
from bio_harness.core.template_assistance_policy import protocol_normalization_policy


_STRUCTURED_UNPRODUCTIVE_TRACE_REASONS = frozenset(
    {
        "candidate_does_not_advance_branch_frontier",
        "candidate_duplicates_completed_prefix",
        "structured_trace_candidate_not_mapping",
        "structured_trace_has_no_candidate_steps",
    }
)


def _planner_trace_unproductive_limit_exceeded(
    *,
    reason: str,
    count: int,
    limit: int,
) -> bool:
    """Return whether repeated rejected structured traces should end an attempt."""

    normalized_reason = str(reason or "").strip()
    if normalized_reason not in _STRUCTURED_UNPRODUCTIVE_TRACE_REASONS:
        return False
    return int(limit) > 0 and int(count) >= int(limit)


def _planner_unproductive_structured_trace_limit() -> int:
    """Return the rejected structured trace limit for one planner attempt."""

    raw = str(os.getenv("BIO_HARNESS_PLANNER_UNPRODUCTIVE_STRUCTURED_TRACE_LIMIT", "2") or "2").strip()
    try:
        value = int(raw)
    except Exception:
        value = 2
    return max(0, min(10, value))


class AgentE2EPlannerSupervisionMixin:
    def _planner_trace_progress_assessment(
        self,
        *,
        planner_trace_dir: str,
        latest_name: str,
    ) -> dict[str, Any]:
        """Return whether a planner trace artifact should extend timeout.

        Args:
            planner_trace_dir: Directory containing planner trace artifacts.
            latest_name: File name of the newest planner trace artifact.

        Returns:
            Mapping with a ``productive`` boolean. Batch planning treats every
            new trace artifact as useful progress; stepwise mode overrides this
            to reject completed-prefix emissions as unproductive.
        """

        execution_mode = str(getattr(self.cfg, "execution_mode", "") or "").strip().lower()
        hook = getattr(self, "_stepwise_planner_trace_progress_assessment", None)
        if execution_mode == "stepwise" and callable(hook):
            return hook(planner_trace_dir=planner_trace_dir, latest_name=latest_name)
        return {"productive": True, "reason": ""}

    def _planner_timeout_trace_resume_max_missing_steps(self) -> int:
        """Return the maximum number of missing workflow steps to resume."""

        raw = str(os.getenv("BIO_HARNESS_PLANNER_TRACE_RESUME_MAX_MISSING_STEPS", "2") or "2").strip()
        try:
            value = int(raw)
        except Exception:
            value = 2
        return max(0, min(4, value))

    def _planner_timeout_trace_recovery_candidate(
        self,
        *,
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        """Recover a near-complete hierarchical plan from planner trace artifacts."""

        planner_trace_dir = str(self.run.get("run_files", {}).get("planner", "") or "").strip()
        recovery_state = load_hierarchical_trace_recovery_state(planner_trace_dir)
        if recovery_state is None:
            return None, "planner_trace_resume_unavailable", {"why": "no_hierarchical_trace_state"}

        step_specs_by_id = {
            int(step_id): dict(step_spec)
            for step_id, step_spec in recovery_state.completed_step_specs_by_id.items()
            if isinstance(step_spec, dict)
        }
        missing_steps = [dict(step) for step in recovery_state.missing_workflow_steps if isinstance(step, dict)]
        recovered_step_ids: list[int] = []

        if missing_steps:
            max_missing_steps = self._planner_timeout_trace_resume_max_missing_steps()
            if len(missing_steps) > max_missing_steps:
                return None, "planner_trace_resume_unavailable", {
                    "why": "missing_step_count_exceeds_limit",
                    "missing_step_ids": [
                        int(step.get("step_id", 0) or 0) for step in missing_steps if isinstance(step, dict)
                    ],
                    "max_missing_steps": int(max_missing_steps),
                    "trace_attempt": int(recovery_state.supervisor_attempt),
                    "trace_pid": int(recovery_state.planner_pid),
                }
            available_skills = []
            try:
                available_skills = self.orchestrator._available_skill_metadata()
            except Exception:
                available_skills = []
            llm = getattr(self.orchestrator, "biollm", None)
            expand_step = getattr(llm, "_expand_workflow_step", None)
            if not callable(expand_step):
                return None, "planner_trace_resume_unavailable", {
                    "why": "step_expansion_api_unavailable",
                    "missing_step_ids": [
                        int(step.get("step_id", 0) or 0) for step in missing_steps if isinstance(step, dict)
                    ],
                    "trace_attempt": int(recovery_state.supervisor_attempt),
                    "trace_pid": int(recovery_state.planner_pid),
                }
            analysis_spec = self.run.get("analysis_spec", {})
            if not isinstance(analysis_spec, dict):
                analysis_spec = {}
            for workflow_step in missing_steps:
                step_id = int(workflow_step.get("step_id", 0) or 0)
                recovered = expand_step(
                    user_query=str(getattr(self.cfg, "prompt", "") or ""),
                    workflow_spec=recovery_state.workflow_spec,
                    workflow_step=workflow_step,
                    available_skills=available_skills,
                    analysis_spec=analysis_spec,
                    seed_step={},
                    model_override=None,
                )
                if not isinstance(recovered, dict):
                    return None, "planner_trace_resume_unavailable", {
                        "why": "missing_step_expansion_failed",
                        "failed_step_id": int(step_id),
                        "recovered_step_ids": recovered_step_ids,
                        "trace_attempt": int(recovery_state.supervisor_attempt),
                        "trace_pid": int(recovery_state.planner_pid),
                    }
                step_specs_by_id[step_id] = recovered
                recovered_step_ids.append(int(step_id))

        assembled = assemble_executable_plan(
            recovery_state.workflow_spec,
            [step_specs_by_id[step_id] for step_id in sorted(step_specs_by_id)],
            analysis_spec=self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {},
            seed_plan=None,
        )
        normalized_candidate, candidate_canonical_meta, _candidate_fc_meta = self._normalize_plan_for_execution(assembled)
        validation = self._assess_contract_for_plan(normalized_candidate, contract)
        normalization_meta: dict[str, Any] = {}
        if not bool(validation.get("passed", False)):
            normalized_candidate, validation, normalization_meta = self._maybe_normalize_candidate_for_contract(
                candidate_plan=normalized_candidate,
                contract=contract,
                validation=validation,
            )
        if not bool(validation.get("passed", False)):
            return None, "planner_trace_resume_unavailable", {
                "why": "recovered_plan_contract_failed",
                "contract_validation": validation,
                "trace_attempt": int(recovery_state.supervisor_attempt),
                "trace_pid": int(recovery_state.planner_pid),
                "workflow_trace_file": recovery_state.workflow_trace_file,
                "recovered_step_ids": recovered_step_ids,
            }
        return normalized_candidate, "timeout_trace_resume", {
            "why": "hierarchical_trace_resumed",
            "trace_attempt": int(recovery_state.supervisor_attempt),
            "trace_pid": int(recovery_state.planner_pid),
            "workflow_trace_file": recovery_state.workflow_trace_file,
            "recovered_step_ids": recovered_step_ids,
            "step_count": len(normalized_candidate.get("plan", []) if isinstance(normalized_candidate, dict) else []),
            "contract_validation": validation,
            "canonicalized_before_contract_check": bool(candidate_canonical_meta.get("changed", False)),
            "protocol_normalized_before_contract_check": bool(normalization_meta.get("changed", False)),
        }

    def _maybe_normalize_candidate_for_contract(
        self,
        *,
        candidate_plan: dict[str, Any],
        contract: dict[str, Any],
        validation: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Rescue compiler-backed plans before strict supervision rejects them."""

        analysis_spec = (
            self.run.get("analysis_spec", {})
            if isinstance(self.run.get("analysis_spec", {}), dict)
            else {}
        )
        protocol_validation = assess_protocol_grounding(candidate_plan, analysis_spec)
        snapshot = build_protocol_normalization_snapshot(
            analysis_spec,
            protocol_validation=protocol_validation,
            template_compiler_types=TEMPLATE_COMPILER_TYPES,
        )
        normalization_enabled, _policy_meta = protocol_normalization_policy(
            benchmark_policy=getattr(self.cfg, "benchmark_policy", None),
            has_compiler=snapshot.has_compiler,
            planning_strict_benchmark_policy=is_bioagentbench_planning_strict_policy(
                getattr(self.cfg, "benchmark_policy", None)
            ),
            protocol_source_files=snapshot.protocol_source_files,
        )
        if not should_attempt_protocol_normalization(
            snapshot,
            normalization_enabled=normalization_enabled,
        ):
            return candidate_plan, validation, {}

        normalized_candidate, norm_meta = deterministic_protocol_repair(
            candidate_plan,
            analysis_spec=analysis_spec,
            selected_dir=self.cfg.selected_dir,
            data_root=self.cfg.data_root,
        )
        if not (isinstance(normalized_candidate, dict) and norm_meta.get("changed", False)):
            return candidate_plan, validation, norm_meta

        normalized_validation = self._assess_contract_for_plan(
            normalized_candidate,
            contract,
        )
        if not bool(normalized_validation.get("passed", False)):
            return candidate_plan, validation, norm_meta
        return normalized_candidate, normalized_validation, norm_meta

    def _planner_trace_latest_artifact(self, planner_trace_dir: str) -> tuple[float, str]:
        """Return the newest planner trace artifact mtime and filename."""

        raw_dir = str(planner_trace_dir or "").strip()
        if not raw_dir:
            return 0.0, ""
        base_dir = Path(raw_dir)
        try:
            if not base_dir.is_dir():
                return 0.0, ""
        except Exception:
            return 0.0, ""

        latest_mtime = 0.0
        latest_name = ""
        try:
            with os.scandir(base_dir) as children:
                for child in children:
                    try:
                        if not child.is_file():
                            continue
                        mtime = float(child.stat().st_mtime)
                    except OSError:
                        continue
                    if mtime >= latest_mtime:
                        latest_mtime = mtime
                        latest_name = str(getattr(child, "name", "") or "")
        except OSError:
            return 0.0, ""
        return latest_mtime, latest_name

    def _planner_attempt_with_heartbeat(
        self,
        *,
        prompt: str,
        strategy: str,
        attempt_num: int,
        planner_mode: str = "auto",
        seed_plan: dict[str, Any] | None = None,
        model_override: str | None = None,
        available_skills_metadata_override: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], float]:
        def _llm_diag_snapshot() -> dict[str, Any]:
            diag: dict[str, Any] = {
                "pid": int(os.getpid()),
                "backend_name": str(getattr(self.orchestrator.biollm, "backend_name", "") or ""),
                "backend_label": str(getattr(self.orchestrator.biollm, "backend_label", "") or ""),
                "transport_name": str(getattr(self.orchestrator.biollm, "transport_name", "") or ""),
                "biollm_host": str(getattr(self.orchestrator.biollm, "host", "") or ""),
                "env_bio_harness_ollama_host": str(os.getenv("BIO_HARNESS_OLLAMA_HOST", "") or ""),
                "env_ollama_host": str(os.getenv("OLLAMA_HOST", "") or ""),
                "env_bio_harness_ollama_openai_base_url": str(os.getenv("BIO_HARNESS_OLLAMA_OPENAI_BASE_URL", "") or ""),
                "env_bio_harness_openai_base_url": str(os.getenv("BIO_HARNESS_OPENAI_BASE_URL", "") or ""),
                "env_bio_harness_vllm_base_url": str(os.getenv("BIO_HARNESS_VLLM_BASE_URL", "") or ""),
                "env_bio_harness_mlx_base_url": str(os.getenv("BIO_HARNESS_MLX_BASE_URL", "") or ""),
                "env_bio_harness_openai_api_key_present": bool(str(os.getenv("BIO_HARNESS_OPENAI_API_KEY", "") or "").strip()),
            }
            try:
                diag["fd_count"] = len(list(Path("/dev/fd").iterdir()))
            except Exception:
                diag["fd_count"] = -1
            try:
                diag.update(self.orchestrator.biollm.diagnostics())
            except Exception as exc:
                diag["direct_probe_ok"] = False
                diag["direct_probe_error"] = str(exc)
            return diag

        wait_budget = self._planner_connectivity_wait_seconds()
        if wait_budget > 0:
            deadline = time.monotonic() + float(wait_budget)
            ready = False
            checks = 0
            last_msg = ""
            while time.monotonic() < deadline:
                checks += 1
                try:
                    ok, msg = self.orchestrator.biollm.is_available()
                except Exception as exc:
                    ok = False
                    msg = str(exc)
                last_msg = str(msg or "")
                if ok:
                    ready = True
                    break
                time.sleep(min(2.0, max(0.25, float(checks) * 0.25)))
            self._append_event(
                step_id=None,
                agent="PlannerSupervisor",
                event_type="PLANNER_CONNECTIVITY_CHECK",
                severity="info" if ready else "warning",
                payload={
                    "attempt": int(attempt_num),
                    "strategy": strategy,
                    "ready": bool(ready),
                    "checks": int(checks),
                    "wait_budget_seconds": int(wait_budget),
                    "last_message": last_msg,
                    "diagnostics": {} if ready else _llm_diag_snapshot(),
                },
            )
        started = time.monotonic()
        interval = self._planner_heartbeat_seconds()
        hard_timeout = self._planner_attempt_timeout_seconds(strategy=strategy, prompt=prompt)
        self._set_planner_phase(
            active=True,
            strategy=strategy,
            timeout_seconds=hard_timeout,
        )
        planner_trace_dir = str(self.run.get("run_files", {}).get("planner", "") or "")
        planner_trace_context = {
            "run_id": str(self.run.get("run_uid", "")),
            "supervisor_attempt": int(attempt_num),
            "strategy": str(strategy),
            "analysis_type": str((self.run.get("analysis_spec", {}) or {}).get("analysis_type", "")),
        }
        self.orchestrator.configure_planner_trace(planner_trace_dir, planner_trace_context)
        deadline = (started + float(hard_timeout)) if hard_timeout > 0 else None
        progress_grace_seconds = self._planner_progress_grace_seconds(base_timeout=hard_timeout)
        progress_max_extension_seconds = self._planner_progress_max_extension_seconds(base_timeout=hard_timeout)
        progress_poll_seconds = self._planner_progress_poll_seconds()
        trace_scan_error_state = {"last_error": ""}

        def _safe_planner_trace_latest_artifact() -> tuple[float, str]:
            try:
                return self._planner_trace_latest_artifact(planner_trace_dir)
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                if error_text != trace_scan_error_state["last_error"]:
                    trace_scan_error_state["last_error"] = error_text
                    self._append_event(
                        step_id=None,
                        agent="PlannerSupervisor",
                        event_type="PLANNER_TRACE_PROGRESS_SCAN_SKIPPED",
                        severity="warning",
                        payload={
                            "attempt": int(attempt_num),
                            "strategy": str(strategy),
                            "error": error_text,
                        },
                    )
                return 0.0, ""

        initial_trace_mtime, _initial_trace_name = _safe_planner_trace_latest_artifact()
        progress_state: dict[str, Any] = {
            "enabled": bool(
                deadline is not None
                and str(planner_trace_dir or "").strip()
                and progress_grace_seconds > 0
                and progress_max_extension_seconds > 0
            ),
            "deadline": float(deadline) if deadline is not None else None,
            "max_deadline": (float(deadline) + float(progress_max_extension_seconds)) if deadline is not None else None,
            "last_trace_mtime": float(initial_trace_mtime),
            "last_progress_at": float(started),
            "last_progress_file": "",
            "unproductive_structured_count": 0,
            "unproductive_structured_limit": _planner_unproductive_structured_trace_limit(),
            "grace_seconds": float(progress_grace_seconds),
            "poll_seconds": float(progress_poll_seconds),
            "lock": threading.Lock(),
        }

        def _timeout_error_message() -> str:
            return (
                f"Planner attempt timed out at supervisor wall-clock limit ({int(hard_timeout)}s). "
                "Falling back to recovery strategy."
            )

        def _current_timeout_deadline() -> float | None:
            if not bool(progress_state.get("enabled", False)):
                return deadline
            with progress_state["lock"]:
                current_deadline = progress_state.get("deadline")
            return float(current_deadline) if current_deadline is not None else deadline

        def _maybe_extend_deadline_from_progress(*, now_ts: float | None = None) -> None:
            if not bool(progress_state.get("enabled", False)):
                return
            latest_mtime, latest_name = _safe_planner_trace_latest_artifact()
            if latest_mtime <= 0.0:
                return
            should_emit = False
            old_deadline = 0.0
            new_deadline = 0.0
            observed_now = float(now_ts if now_ts is not None else time.monotonic())
            with progress_state["lock"]:
                last_trace_mtime = float(progress_state.get("last_trace_mtime", 0.0) or 0.0)
                if latest_mtime <= (last_trace_mtime + 1e-6):
                    return
                progress_state["last_trace_mtime"] = float(latest_mtime)
                progress_state["last_progress_file"] = str(latest_name or "")
            assessment = self._planner_trace_progress_assessment(
                planner_trace_dir=str(planner_trace_dir or ""),
                latest_name=str(latest_name or ""),
            )
            if not bool(assessment.get("productive", True)):
                reason = str(assessment.get("reason", "") or "")
                unproductive_count = 0
                limit_exceeded = False
                with progress_state["lock"]:
                    prior_count = int(progress_state.get("unproductive_structured_count", 0) or 0)
                    if str(reason).strip() in _STRUCTURED_UNPRODUCTIVE_TRACE_REASONS:
                        unproductive_count = prior_count + 1
                        progress_state["unproductive_structured_count"] = unproductive_count
                    else:
                        unproductive_count = prior_count
                    limit_exceeded = _planner_trace_unproductive_limit_exceeded(
                        reason=reason,
                        count=unproductive_count,
                        limit=int(progress_state.get("unproductive_structured_limit", 0) or 0),
                    )
                    if limit_exceeded:
                        current_deadline = progress_state.get("deadline")
                        progress_state["deadline"] = (
                            min(float(current_deadline), observed_now)
                            if current_deadline is not None
                            else observed_now
                        )
                self._append_event(
                    step_id=None,
                    agent="PlannerSupervisor",
                    event_type="PLANNER_TRACE_PROGRESS_UNPRODUCTIVE",
                    severity="info",
                    payload={
                        "attempt": int(attempt_num),
                        "strategy": strategy,
                        "elapsed_seconds": int(max(0.0, observed_now - started)),
                        "progress_file": str(latest_name or ""),
                        "reason": reason,
                        "unproductive_structured_count": int(unproductive_count),
                        "details": assessment.get("details", {}),
                    },
                )
                if limit_exceeded:
                    self._append_event(
                        step_id=None,
                        agent="PlannerSupervisor",
                        event_type="PLANNER_ATTEMPT_UNPRODUCTIVE_TRACE_LIMIT",
                        severity="warning",
                        payload={
                            "attempt": int(attempt_num),
                            "strategy": strategy,
                            "elapsed_seconds": int(max(0.0, observed_now - started)),
                            "progress_file": str(latest_name or ""),
                            "reason": reason,
                            "unproductive_structured_count": int(unproductive_count),
                            "limit": int(progress_state.get("unproductive_structured_limit", 0) or 0),
                        },
                    )
                return
            with progress_state["lock"]:
                progress_state["unproductive_structured_count"] = 0
                progress_state["last_progress_at"] = observed_now
                current_deadline = progress_state.get("deadline")
                max_deadline = progress_state.get("max_deadline")
                if current_deadline is None or max_deadline is None:
                    return
                old_deadline = float(current_deadline)
                new_deadline = min(
                    float(max_deadline),
                    max(float(current_deadline), observed_now + float(progress_state.get("grace_seconds", 0.0) or 0.0)),
                )
                progress_state["deadline"] = new_deadline
                should_emit = new_deadline > (old_deadline + 1e-6)
            if should_emit:
                self._append_event(
                    step_id=None,
                    agent="PlannerSupervisor",
                    event_type="PLANNER_ATTEMPT_TIMEOUT_EXTENDED",
                    severity="info",
                    payload={
                        "attempt": int(attempt_num),
                        "strategy": strategy,
                        "elapsed_seconds": int(max(0.0, observed_now - started)),
                        "timeout_seconds": int(hard_timeout),
                        "effective_timeout_seconds": int(max(0.0, round(new_deadline - started))),
                        "max_extension_seconds": int(progress_max_extension_seconds),
                        "grace_seconds": int(progress_grace_seconds),
                        "progress_file": str(latest_name or ""),
                    },
                )

        process_isolation_requested = self._planner_isolate_process_enabled()
        use_process_isolation = self._planner_process_isolation_allowed()
        planner_start_method = self._planner_process_start_method() if use_process_isolation else ""
        stop_hb = threading.Event()
        self._append_event(
            step_id=None,
            agent="PlannerSupervisor",
            event_type="PLANNER_ATTEMPT_STARTED",
            severity="info",
            payload={
                "attempt": int(attempt_num),
                "strategy": strategy,
                "timeout_seconds": int(hard_timeout),
                "process_isolation": bool(use_process_isolation),
                "process_start_method": planner_start_method,
            },
        )
        def _heartbeat_worker() -> None:
            last_hb = 0.0
            while not stop_hb.is_set():
                now_ts = time.monotonic()
                if (now_ts - last_hb) >= interval:
                    elapsed = int(now_ts - started)
                    _emit(
                        f"[planning-heartbeat] attempt={attempt_num} strategy={strategy} elapsed={elapsed}s",
                        quiet=self.cfg.quiet,
                    )
                    self._append_event(
                        step_id=None,
                        agent="PlannerSupervisor",
                        event_type="PLANNER_HEARTBEAT",
                        severity="info",
                        payload={"attempt": int(attempt_num), "strategy": strategy, "elapsed_seconds": elapsed},
                    )
                    last_hb = now_ts
                stop_hb.wait(1.0)

        hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
        hb_thread.start()
        progress_thread: threading.Thread | None = None
        if bool(progress_state.get("enabled", False)):
            def _progress_worker() -> None:
                poll_seconds = float(progress_state.get("poll_seconds", 0.5) or 0.5)
                while not stop_hb.is_set():
                    _maybe_extend_deadline_from_progress()
                    stop_hb.wait(poll_seconds)

            progress_thread = threading.Thread(target=_progress_worker, daemon=True)
            progress_thread.start()

        plan: dict[str, Any] = {}
        if process_isolation_requested and not use_process_isolation:
            self._append_event(
                step_id=None,
                agent="PlannerSupervisor",
                event_type="PLANNER_PROCESS_ISOLATION_SKIPPED",
                severity="info",
                payload={
                    "attempt": int(attempt_num),
                    "strategy": strategy,
                    "reason": "planner_callable_overridden_or_isolation_disabled",
                },
            )
        worker_proc: mp.Process | None = None
        worker_parent_conn: Any = None
        worker_child_conn: Any = None
        if use_process_isolation:
            ctx = mp.get_context(planner_start_method or "spawn")
            worker_parent_conn, worker_child_conn = ctx.Pipe(duplex=False)
            worker_proc = ctx.Process(
                target=_planner_worker_think,
                args=(
                    prompt,
                    self.cfg.model_name,
                    self.cfg.host,
                    self.cfg.llm_backend,
                    worker_child_conn,
                    self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {},
                    planner_mode,
                    seed_plan if isinstance(seed_plan, dict) else None,
                    planner_trace_dir,
                    planner_trace_context,
                    model_override,
                    [dict(item) for item in available_skills_metadata_override]
                    if isinstance(available_skills_metadata_override, list)
                    else None,
                ),
                daemon=True,
            )
            worker_proc.start()
            self._update_planner_phase_pid(int(getattr(worker_proc, "pid", 0) or 0))
            if worker_child_conn is not None:
                try:
                    worker_child_conn.close()
                except Exception:
                    pass
                worker_child_conn = None
            worker_result_queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
            worker_reader_thread: threading.Thread | None = None

            def _start_worker_result_reader() -> None:
                if worker_parent_conn is None:
                    return

                def _reader() -> None:
                    try:
                        ready = worker_parent_conn.poll(None)
                    except TypeError:
                        try:
                            ready = worker_parent_conn.poll()
                        except Exception as exc:
                            worker_result_queue.put(("poll_error", exc))
                            return
                    except Exception as exc:
                        worker_result_queue.put(("poll_error", exc))
                        return
                    if not ready:
                        worker_result_queue.put(("empty", None))
                        return
                    try:
                        worker_result_queue.put(("result", worker_parent_conn.recv()))
                    except EOFError:
                        worker_result_queue.put(("eof", None))
                    except Exception as exc:
                        worker_result_queue.put(("recv_error", exc))

                nonlocal worker_reader_thread
                worker_reader_thread = threading.Thread(target=_reader, daemon=True)
                worker_reader_thread.start()

            def _drain_worker_result() -> tuple[str, Any] | None:
                try:
                    return worker_result_queue.get_nowait()
                except queue.Empty:
                    return None

            _start_worker_result_reader()
            isolation_error: Exception | None = None
            try:
                worker_exit_observed_at: float | None = None
                while True:
                    now_ts = time.monotonic()
                    _maybe_extend_deadline_from_progress(now_ts=now_ts)
                    deadline = _current_timeout_deadline()
                    remaining = (deadline - now_ts) if deadline is not None else None
                    if remaining is not None and remaining <= 0:
                        raise TimeoutError(_timeout_error_message())
                    result_message = _drain_worker_result()
                    result = result_message[1] if result_message is not None else None
                    if isinstance(result, dict):
                        if bool(result.get("ok", False)):
                            candidate = result.get("plan", {})
                            plan = candidate if isinstance(candidate, dict) else {}
                            break
                        err = str(result.get("error", "")).strip()
                        if "timed out" in err.lower() or "timeout" in err.lower():
                            raise TimeoutError(err or "Planner timed out.")
                        raise RuntimeError(err or "Planner worker failed.")
                    if result_message is not None and result_message[0] in {"poll_error", "recv_error"}:
                        raise RuntimeError(f"Planner worker communication failed: {result_message[1]}")

                    worker_exitcode = getattr(worker_proc, "exitcode", None) if worker_proc is not None else None
                    worker_alive = bool(
                        worker_proc is not None and worker_exitcode is None and worker_proc.is_alive()
                    )
                    if worker_proc is not None and (not worker_alive):
                        if worker_exit_observed_at is None:
                            worker_exit_observed_at = now_ts
                        elif (now_ts - worker_exit_observed_at) >= 0.25:
                            raise RuntimeError("Planner worker exited unexpectedly without result.")
                    else:
                        worker_exit_observed_at = None

                    sleep_for = 0.1 if remaining is None else max(0.0, min(0.1, remaining))
                    if sleep_for > 0:
                        time.sleep(sleep_for)
            except TimeoutError:
                elapsed = int(max(0.0, time.monotonic() - started))
                self._append_event(
                    step_id=None,
                    agent="PlannerSupervisor",
                    event_type="PLANNER_ATTEMPT_TIMEOUT_FORCED",
                    severity="warning",
                    payload={
                        "attempt": int(attempt_num),
                        "strategy": strategy,
                        "elapsed_seconds": elapsed,
                        "timeout_seconds": int(hard_timeout),
                        "enforcement_mode": "process_isolation",
                    },
                )
                raise
            except Exception as exc:
                isolation_error = exc
            finally:
                stop_hb.set()
                hb_thread.join(timeout=1.0)
                if progress_thread is not None:
                    progress_thread.join(timeout=1.0)
                if worker_proc is not None and worker_proc.is_alive():
                    worker_proc.terminate()
                    worker_proc.join(timeout=2.0)
                    if worker_proc.is_alive():
                        worker_proc.kill()
                        worker_proc.join(timeout=2.0)
                if worker_parent_conn is not None:
                    try:
                        worker_parent_conn.close()
                    except Exception:
                        pass
                if worker_child_conn is not None:
                    try:
                        worker_child_conn.close()
                    except Exception:
                        pass
            self._set_planner_phase(active=False)

            if isolation_error is not None:
                if self._is_model_server_connectivity_error(str(isolation_error)):
                    self._append_event(
                        step_id=None,
                        agent="PlannerSupervisor",
                        event_type="PLANNER_PROCESS_ISOLATION_FALLBACK",
                        severity="warning",
                        payload={
                            "attempt": int(attempt_num),
                            "strategy": strategy,
                            "reason": "model_server_connectivity_error",
                            "error": str(isolation_error),
                        },
                    )
                    use_process_isolation = False
                else:
                    raise isolation_error

            if use_process_isolation:
                elapsed = max(0.0, time.monotonic() - started)
                if not isinstance(plan, dict):
                    raise ValueError("Planner returned non-dict output.")
                return plan, elapsed

            # Planner process isolation encountered a backend connectivity error.
            # Continue with in-process planning path below to preserve execution.
            self._set_planner_phase(
                active=True,
                strategy=strategy,
                timeout_seconds=hard_timeout,
            )

        use_signal_timeout = bool(
            hard_timeout > 0
            and threading.current_thread() is threading.main_thread()
            and hasattr(signal, "SIGALRM")
            and hasattr(signal, "setitimer")
        )
        prev_handler: Any = None
        prev_timer: tuple[float, float] | None = None
        try:
            if use_signal_timeout:
                def _alarm_handler(_signum: int, _frame: Any) -> None:
                    now_ts = time.monotonic()
                    _maybe_extend_deadline_from_progress(now_ts=now_ts)
                    current_deadline = _current_timeout_deadline()
                    if current_deadline is not None and now_ts < (float(current_deadline) - 1e-6):
                        remaining = max(0.1, float(current_deadline) - now_ts)
                        interval = min(
                            max(0.1, float(progress_state.get("poll_seconds", 0.5) or 0.5)),
                            remaining,
                        )
                        signal.setitimer(signal.ITIMER_REAL, interval)
                        return
                    raise TimeoutError(_timeout_error_message())

                prev_handler = signal.getsignal(signal.SIGALRM)
                prev_timer = signal.setitimer(signal.ITIMER_REAL, 0.0)
                signal.signal(signal.SIGALRM, _alarm_handler)
                initial_deadline = _current_timeout_deadline()
                if initial_deadline is not None:
                    remaining = max(0.1, float(initial_deadline) - time.monotonic())
                    if bool(progress_state.get("enabled", False)):
                        remaining = min(remaining, max(0.1, float(progress_state.get("poll_seconds", 0.5) or 0.5)))
                    signal.setitimer(signal.ITIMER_REAL, remaining)

            try:
                plan = self.orchestrator.think(
                    prompt,
                    analysis_spec=self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {},
                    planner_mode=planner_mode,
                    seed_plan=seed_plan if isinstance(seed_plan, dict) else None,
                    model_override=model_override,
                )
            except TypeError as exc:
                msg = str(exc)
                if "unexpected keyword argument 'planner_mode'" not in msg and "unexpected keyword argument 'seed_plan'" not in msg:
                    raise
                plan = self.orchestrator.think(
                    prompt,
                    analysis_spec=self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {},
                )
        except TimeoutError:
            elapsed = int(max(0.0, time.monotonic() - started))
            self._append_event(
                step_id=None,
                agent="PlannerSupervisor",
                event_type="PLANNER_ATTEMPT_TIMEOUT_FORCED",
                severity="warning",
                payload={
                    "attempt": int(attempt_num),
                    "strategy": strategy,
                    "elapsed_seconds": elapsed,
                    "timeout_seconds": int(hard_timeout),
                },
            )
            raise
        finally:
            if use_signal_timeout:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
                if prev_handler is not None:
                    signal.signal(signal.SIGALRM, prev_handler)
                if prev_timer is not None and (prev_timer[0] > 0 or prev_timer[1] > 0):
                    signal.setitimer(signal.ITIMER_REAL, prev_timer[0], prev_timer[1])
            stop_hb.set()
            hb_thread.join(timeout=1.0)
            if progress_thread is not None:
                progress_thread.join(timeout=1.0)
            self._set_planner_phase(active=False)

        elapsed = max(0.0, time.monotonic() - started)
        if not isinstance(plan, dict):
            raise ValueError("Planner returned non-dict output.")
        return plan, elapsed

    def _generate_plan_with_supervision(self, contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        fastpath_plan, fastpath_meta = self._planner_template_fastpath_candidate(contract=contract)
        if isinstance(fastpath_plan, dict):
            row = {
                "attempt": 0,
                "strategy": "template_fastpath",
                "status": "succeeded",
                "step_count": len(fastpath_plan.get("plan", []) if isinstance(fastpath_plan, dict) else []),
                "contract_passed": True,
                "missing_capabilities": [],
                "missing_tool_hints": [],
            }
            self.run["planning_attempts"] = [row]
            self.run["planner_strategy_used"] = "template_fastpath"
            self._append_event(
                step_id=None,
                agent="PlannerSupervisor",
                event_type="PLANNER_TEMPLATE_FASTPATH_APPLIED",
                severity="info",
                payload={"attempt": 0, "strategy": "template_fastpath", "details": fastpath_meta},
            )
            return fastpath_plan, {
                "attempts": [row],
                "strategy": "template_fastpath",
                "contract_validation": fastpath_meta.get("contract_validation", {}),
                "fastpath": fastpath_meta,
            }

        self._maybe_prewarm_planner()
        attempts: list[dict[str, Any]] = []
        max_attempts = self._planner_max_attempts()
        latest_plan: dict[str, Any] | None = None
        latest_validation: dict[str, Any] = {
            "passed": False,
            "missing_capabilities": [],
            "missing_tool_hints": [],
            "direct_wrapper_issues": [],
        }
        last_exc: Exception | None = None

        for attempt_num in range(1, max_attempts + 1):
            if attempt_num == 1:
                strategy = "direct_user_prompt"
                prompt = self._initial_planning_prompt()
            elif attempt_num == 2:
                strategy = "contract_focus_prompt"
                prompt = self._planner_contract_focus_prompt(
                    contract=contract,
                    latest_validation=latest_validation,
                    prior_plan=latest_plan,
                )
            else:
                strategy = "contract_repair_prompt"
                prompt = self._contract_replan_prompt(
                    contract=contract,
                    validation=latest_validation,
                    plan=latest_plan or {},
                )

            try:
                candidate_plan, elapsed = self._planner_attempt_with_heartbeat(
                    prompt=prompt,
                    strategy=strategy,
                    attempt_num=attempt_num,
                    planner_mode="auto",
                    seed_plan=latest_plan if isinstance(latest_plan, dict) else None,
                )
            except Exception as exc:
                last_exc = exc
                err_text = str(exc)
                timed_out = "timed out" in err_text.lower()
                loopback_blocked = self._is_local_model_loopback_blocked(exc)
                if timed_out:
                    self.run["planner_timeout_detected"] = True
                    self._note_failure_signature("planner_timeout")
                if loopback_blocked:
                    self.run["local_model_loopback_blocked_detected"] = True
                    self._note_failure_signature("local_model_loopback_blocked")
                row = {
                    "attempt": int(attempt_num),
                    "strategy": strategy,
                    "status": "failed",
                    "error": err_text,
                    "local_model_loopback_blocked": bool(loopback_blocked),
                }
                attempts.append(row)
                self._append_event(
                    step_id=None,
                    agent="PlannerSupervisor",
                    event_type="PLANNER_ATTEMPT_FAILED",
                    severity="warning",
                    payload=row,
                )
                if timed_out:
                    recovered_plan, recovered_strategy, recovered_details = self._planner_timeout_trace_recovery_candidate(
                        contract=contract,
                    )
                    if isinstance(recovered_plan, dict):
                        self.run["planning_attempts"] = attempts
                        self.run["planner_strategy_used"] = recovered_strategy
                        self._append_event(
                            step_id=None,
                            agent="PlannerSupervisor",
                            event_type="PLANNER_TIMEOUT_TRACE_RECOVERY_APPLIED",
                            severity="warning",
                            payload={
                                "attempt": int(attempt_num),
                                "strategy": strategy,
                                "recovery_strategy": recovered_strategy,
                                "details": recovered_details,
                            },
                        )
                        return recovered_plan, {
                            "attempts": attempts,
                            "strategy": recovered_strategy,
                            "contract_validation": recovered_details.get("contract_validation", {}),
                            "trace_recovery": recovered_details,
                        }
                    failopen_plan, failopen_strategy, failopen_details = self._planner_timeout_failopen_candidate(
                        contract=contract,
                    )
                    if isinstance(failopen_plan, dict):
                        self.run["planner_failopen_used"] = True
                        self.run["planning_attempts"] = attempts
                        self.run["planner_strategy_used"] = failopen_strategy
                        self._append_event(
                            step_id=None,
                            agent="PlannerSupervisor",
                            event_type="PLANNER_TIMEOUT_FAILOPEN_APPLIED",
                            severity="warning",
                            payload={
                                "attempt": int(attempt_num),
                                "strategy": strategy,
                                "failopen_strategy": failopen_strategy,
                                "details": failopen_details,
                            },
                        )
                        return failopen_plan, {
                            "attempts": attempts,
                            "strategy": failopen_strategy,
                            "contract_validation": failopen_details.get("contract_validation", {}),
                            "failopen": failopen_details,
                        }
                continue

            normalized_candidate, candidate_canonical_meta, _candidate_fc_meta = self._normalize_plan_for_execution(candidate_plan)
            latest_plan = normalized_candidate
            latest_validation = self._assess_contract_for_plan(normalized_candidate, contract)
            passed = bool(latest_validation.get("passed", False))
            normalization_meta: dict[str, Any] = {}
            if not passed:
                (
                    normalized_candidate,
                    latest_validation,
                    normalization_meta,
                ) = self._maybe_normalize_candidate_for_contract(
                    candidate_plan=normalized_candidate,
                    contract=contract,
                    validation=latest_validation,
                )
                latest_plan = normalized_candidate
                passed = bool(latest_validation.get("passed", False))
            row = {
                "attempt": int(attempt_num),
                "strategy": strategy,
                "status": "succeeded",
                "elapsed_seconds": round(float(elapsed), 3),
                "step_count": len(normalized_candidate.get("plan", []) if isinstance(normalized_candidate, dict) else []),
                "contract_passed": passed,
                "missing_capabilities": list(latest_validation.get("missing_capabilities", [])),
                "missing_tool_hints": list(latest_validation.get("missing_tool_hints", [])),
                "direct_wrapper_issues": list(latest_validation.get("direct_wrapper_issues", [])),
                "artifact_role_issues": list(latest_validation.get("artifact_role_issues", [])),
            }
            if candidate_canonical_meta.get("changed", False):
                row["canonicalized_before_contract_check"] = True
            if normalization_meta.get("changed", False):
                row["protocol_normalized_before_contract_check"] = True
            attempts.append(row)
            self._append_event(
                step_id=None,
                agent="PlannerSupervisor",
                event_type="PLANNER_ATTEMPT_SUCCEEDED",
                severity="info",
                payload=row,
            )
            if passed:
                self.run["planning_attempts"] = attempts
                self.run["planner_strategy_used"] = strategy
                return normalized_candidate, {
                    "attempts": attempts,
                    "strategy": strategy,
                    "contract_validation": latest_validation,
                }

        self.run["planning_attempts"] = attempts
        self.run["planner_strategy_used"] = ""
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Planner did not produce a usable plan.")
