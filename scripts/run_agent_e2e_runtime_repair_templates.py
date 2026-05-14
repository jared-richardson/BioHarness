from __future__ import annotations

from scripts.run_agent_e2e_support import (
    Any,
    HEAVY_TOOL_NAMES,
    MAX_COMPOSED_FALLBACK_SEGMENTS,
    MAX_REPLAN_STEP_DELTA,
    _compose_plan_segments,
    _extract_bam_list_paths_from_plan,
    _extract_group_tags_from_request_text,
    _extract_sample_tags_from_plan,
    _extract_selection_pipeline_id,
    _failed_tool_name,
    _first_failed_step_number,
    _is_empty_contract,
    _normalize_capability_list,
    _normalize_steps,
    _renumber_plan_steps,
    _resolve_reference_paths_for_template_fallback,
    _step_fingerprint,
    select_ranked_fallback_plan,
    shlex,
    shutil,
)


class AgentE2ERuntimeRepairTemplateMixin:
    def _prune_and_bound_replan_candidate(
        self,
        candidate: dict[str, Any],
        failure_class: str,
        before_steps: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        old_plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        old_steps = _normalize_steps(old_plan)
        old_statuses = self.run.get("step_statuses", [])
        prefix_len = 0
        for st in old_statuses:
            if str(st).strip().lower() == "completed":
                prefix_len += 1
            else:
                break
        prefix_len = min(prefix_len, len(old_steps))
        failed_step_num = _first_failed_step_number(
            old_statuses,
            fallback_next_idx=int(self.run.get("next_step_idx", 0)),
        )
        failed_tool = _failed_tool_name(old_plan, failed_step_num)
        allow_heavy_rerun = failed_tool in HEAVY_TOOL_NAMES or failure_class in {"missing_reference", "format_input_error"}

        candidate_steps = _normalize_steps(candidate)
        removed_prefix = 0
        while (
            removed_prefix < prefix_len
            and removed_prefix < len(candidate_steps)
            and _step_fingerprint(candidate_steps[removed_prefix]) == _step_fingerprint(old_steps[removed_prefix])
        ):
            removed_prefix += 1
        if removed_prefix > 0:
            candidate_steps = candidate_steps[removed_prefix:]

        removed_heavy = 0
        if prefix_len > 0 and not allow_heavy_rerun:
            prefix_heavy_fps = {
                _step_fingerprint(old_steps[i])
                for i in range(prefix_len)
                if str(old_steps[i].get("tool_name", "")).strip().lower() in HEAVY_TOOL_NAMES
            }
            filtered_steps: list[dict[str, Any]] = []
            for step in candidate_steps:
                tool = str(step.get("tool_name", "")).strip().lower()
                if tool in HEAVY_TOOL_NAMES and _step_fingerprint(step) in prefix_heavy_fps:
                    removed_heavy += 1
                    continue
                filtered_steps.append(step)
            candidate_steps = filtered_steps

        patched = dict(candidate) if isinstance(candidate, dict) else {}
        patched["plan"] = candidate_steps
        patched = _renumber_plan_steps(patched)
        after_steps = len(candidate_steps)
        growth = after_steps - int(before_steps)
        heavy_reintroduced = False
        if prefix_len > 0 and not allow_heavy_rerun:
            heavy_reintroduced = any(
                str(step.get("tool_name", "")).strip().lower() in HEAVY_TOOL_NAMES for step in candidate_steps
            )

        return patched, {
            "failed_step_number": failed_step_num,
            "failed_tool": failed_tool,
            "allow_heavy_rerun": bool(allow_heavy_rerun),
            "removed_replayed_prefix_steps": int(removed_prefix),
            "removed_replayed_heavy_steps": int(removed_heavy),
            "before_step_count": int(before_steps),
            "after_step_count": int(after_steps),
            "step_growth": int(growth),
            "max_step_growth": int(MAX_REPLAN_STEP_DELTA),
            "heavy_reintroduced": bool(heavy_reintroduced),
        }

    def _rewrite_variant_segment_for_inplan_bam_list(
        self,
        *,
        base_plan: dict[str, Any],
        candidate_plan: dict[str, Any],
        candidate_pipeline_id: str,
        reference_fasta: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        pipeline_id = str(candidate_pipeline_id or "").strip()
        if not pipeline_id.startswith("germline_variant_"):
            return candidate_plan, {"changed": False, "why": "pipeline_not_germline_variant"}
        steps = _normalize_steps(candidate_plan)
        if not steps:
            return candidate_plan, {"changed": False, "why": "candidate_plan_missing"}
        has_bwa_align = any(str(s.get("tool_name", "")).strip().lower() == "bwa_mem_align" for s in steps)
        caller_tools = {
            str(s.get("tool_name", "")).strip().lower()
            for s in steps
            if str(s.get("tool_name", "")).strip()
        }
        supported_callers = {"bcftools_call", "gatk_haplotypecaller", "freebayes_call", "varscan_call"}
        active_callers = sorted(caller_tools.intersection(supported_callers))
        if not (has_bwa_align and active_callers):
            return candidate_plan, {"changed": False, "why": "segment_not_bwa_plus_supported_variant_caller"}
        if shutil.which("bwa") or shutil.which("bwa-mem2"):
            return candidate_plan, {"changed": False, "why": "bwa_available_no_rewrite"}

        bam_list_paths = _extract_bam_list_paths_from_plan(base_plan)
        if not bam_list_paths:
            return candidate_plan, {"changed": False, "why": "no_inplan_bam_list_detected"}
        preferred = sorted(
            bam_list_paths,
            key=lambda p: (
                0 if "control" in str(p).lower() else 1,
                len(str(p)),
                str(p),
            ),
        )[0]
        out_base = f"outputs/fallback/{pipeline_id}"
        linked_bam = f"{out_base}/input_from_inplan.bam"
        linked_bai = f"{linked_bam}.bai"
        bam_list_q = shlex.quote(preferred)
        out_base_q = shlex.quote(out_base)
        linked_bam_q = shlex.quote(linked_bam)
        linked_bai_q = shlex.quote(linked_bai)
        resolve_cmd = (
            "set -euo pipefail; "
            f"bam_list={bam_list_q}; "
            "if [ ! -s \"$bam_list\" ]; then echo '__MISSING_BAM_LIST__'; exit 2; fi; "
            "bam=\"$(grep -m1 -v '^[[:space:]]*$' \"$bam_list\" || true)\"; "
            "bam=\"${bam%$'\\r'}\"; "
            "case \"$bam\" in /*) ;; *) bam=\"$PWD/$bam\" ;; esac; "
            "if [ -z \"$bam\" ] || [ ! -f \"$bam\" ]; then echo '__MISSING_BAM_FROM_LIST__'; exit 2; fi; "
            f"mkdir -p {out_base_q}; "
            f"ln -sf \"$bam\" {linked_bam_q}; "
            f"if [ -f \"$bam.bai\" ]; then ln -sf \"$bam.bai\" {linked_bai_q}; "
            f"elif [ -f \"${{bam%.bam}}.bai\" ]; then ln -sf \"${{bam%.bam}}.bai\" {linked_bai_q}; fi"
        )

        rewritten_steps: list[dict[str, Any]] = []
        inserted_resolver = False
        for step in steps:
            tool = str(step.get("tool_name", "")).strip().lower()
            if tool == "bwa_mem_align":
                continue
            row = dict(step)
            row.pop("step_id", None)
            if tool in supported_callers:
                if not inserted_resolver:
                    rewritten_steps.append({"tool_name": "bash_run", "arguments": {"command": resolve_cmd}})
                    inserted_resolver = True
                args = dict(row.get("arguments", {}) if isinstance(row.get("arguments", {}), dict) else {})
                if str(reference_fasta or "").strip():
                    args["reference_fasta"] = str(reference_fasta)
                args["input_bam"] = linked_bam
                row["arguments"] = args
            rewritten_steps.append(row)

        if not inserted_resolver:
            return candidate_plan, {"changed": False, "why": "variant_caller_step_not_found_for_rewire"}
        patched = dict(candidate_plan)
        patched["plan"] = rewritten_steps
        patched = _renumber_plan_steps(patched)
        thought = str(patched.get("thought_process", "")).strip()
        suffix = "Rewired to in-plan BAM list provenance because bwa is unavailable."
        patched["thought_process"] = f"{thought} {suffix}".strip()
        return patched, {
            "changed": True,
            "why": "rewired_variant_segment_to_inplan_bam_list",
            "candidate_pipeline_id": pipeline_id,
            "bam_list_path": preferred,
            "linked_bam": linked_bam,
            "caller_tools": active_callers,
        }

    def _compose_contract_template_plan(
        self,
        *,
        base_plan: dict[str, Any],
        base_selection: dict[str, Any],
        contract: dict[str, Any],
        prompt: str,
        data_root: str,
        selected_dir: str,
        reference_fasta: str,
        annotation_gtf: str,
        control_tag: str,
        treatment_tag: str,
        subset_mode: bool,
        test_reads_per_fastq: int,
        cache_paths: dict[str, str],
        preference_profile: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        initial_validation = self._assess_repair_contract_for_plan(base_plan, contract)
        initial_missing = _normalize_capability_list(initial_validation.get("missing_capabilities", []))
        if not initial_missing:
            return base_plan, {
                "attempted": False,
                "applied": False,
                "why": "single_template_satisfies_contract",
                "selected_pipeline_ids": [],
                "initial_missing_capabilities": [],
                "final_missing_capabilities": [],
                "segments": [],
            }

        base_pipeline_id = _extract_selection_pipeline_id(base_selection, base_plan) or "segment_01"
        segment_plans: list[dict[str, Any]] = [base_plan]
        segment_ids: list[str] = [base_pipeline_id]
        segment_rows: list[dict[str, Any]] = []
        excluded: set[str] = {base_pipeline_id}
        remaining = list(initial_missing)
        stop_reason = "max_templates_reached"

        while remaining and len(segment_plans) < MAX_COMPOSED_FALLBACK_SEGMENTS:
            remaining_contract = {
                "must_include_capabilities": list(remaining),
                "explicit_tool_hints": list(contract.get("explicit_tool_hints", [])),
            }
            candidate_plan, candidate_selection = select_ranked_fallback_plan(
                contract=remaining_contract,
                prompt=prompt,
                data_root=data_root,
                selected_dir=selected_dir,
                reference_fasta=reference_fasta,
                annotation_gtf=annotation_gtf,
                control_tag=control_tag,
                treatment_tag=treatment_tag,
                subset_mode=subset_mode,
                test_reads_per_fastq=test_reads_per_fastq,
                cache_paths=cache_paths,
                excluded_pipeline_ids=sorted(excluded),
                graph_store=self.path_graph,
                preference_profile=preference_profile,
                provenance_mode="fresh_alignment",
            )
            if not isinstance(candidate_plan, dict):
                stop_reason = "catalog_selection_unavailable"
                break

            candidate_pipeline_id = _extract_selection_pipeline_id(candidate_selection, candidate_plan)
            candidate_key = candidate_pipeline_id or f"segment_{len(segment_plans) + 1:02d}"
            if candidate_key in excluded:
                stop_reason = "selector_returned_excluded_template"
                break

            candidate_plan, rewire_meta = self._rewrite_variant_segment_for_inplan_bam_list(
                base_plan=base_plan,
                candidate_plan=candidate_plan,
                candidate_pipeline_id=candidate_key,
                reference_fasta=reference_fasta,
            )

            candidate_validation = self._assess_repair_contract_for_plan(
                candidate_plan,
                remaining_contract,
            )
            candidate_missing = _normalize_capability_list(candidate_validation.get("missing_capabilities", []))
            newly_covered = sorted(set(remaining).difference(set(candidate_missing)))
            if not newly_covered:
                stop_reason = "no_additional_coverage"
                break

            segment_plans.append(candidate_plan)
            segment_ids.append(candidate_key)
            excluded.add(candidate_key)
            remaining = list(candidate_missing)
            segment_rows.append(
                {
                    "pipeline_id": candidate_key,
                    "selection_reason": str(candidate_selection.get("selection_reason", "")),
                    "newly_covered_capabilities": newly_covered,
                    "remaining_capabilities": list(remaining),
                    "provenance_rewire": rewire_meta,
                }
            )

        if len(segment_plans) <= 1:
            return base_plan, {
                "attempted": True,
                "applied": False,
                "why": stop_reason,
                "selected_pipeline_ids": [x for x in segment_ids if x],
                "initial_missing_capabilities": list(initial_missing),
                "final_missing_capabilities": list(initial_missing),
                "segments": segment_rows,
                "max_templates": int(MAX_COMPOSED_FALLBACK_SEGMENTS),
            }

        composed_plan = _compose_plan_segments(
            base_plan=base_plan,
            segment_plans=segment_plans,
            segment_ids=segment_ids,
        )
        final_validation = self._assess_repair_contract_for_plan(composed_plan, contract)
        final_missing = _normalize_capability_list(final_validation.get("missing_capabilities", []))
        improved = len(final_missing) < len(initial_missing)
        if not improved:
            return base_plan, {
                "attempted": True,
                "applied": False,
                "why": "composition_no_coverage_improvement",
                "selected_pipeline_ids": [x for x in segment_ids if x],
                "initial_missing_capabilities": list(initial_missing),
                "final_missing_capabilities": list(initial_missing),
                "segments": segment_rows,
                "max_templates": int(MAX_COMPOSED_FALLBACK_SEGMENTS),
            }

        return composed_plan, {
            "attempted": True,
            "applied": True,
            "why": "composed_templates",
            "selected_pipeline_ids": [x for x in segment_ids if x],
            "initial_missing_capabilities": list(initial_missing),
            "final_missing_capabilities": list(final_missing),
            "segments": segment_rows,
            "final_validation": final_validation,
            "max_templates": int(MAX_COMPOSED_FALLBACK_SEGMENTS),
        }

    def _fallback_provenance_mode(self, contract: dict[str, Any]) -> str:
        required_caps = {
            str(x).strip().lower()
            for x in (contract.get("must_include_capabilities", []) if isinstance(contract, dict) else [])
            if str(x).strip()
        }
        if "alignment" in required_caps and required_caps.intersection({"variant_calling", "cnv_analysis"}):
            return "fresh_alignment"
        return "standard"

    def _build_contract_template_repair(self, failure_class: str) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        if failure_class not in {
            "contract_mismatch",
            "runtime_step_failure",
            "unknown_failure",
            "validation_block",
            "policy_block",
            "format_input_error",
        }:
            return None, "template_not_applicable", {"why": "failure_class_not_supported"}

        fallback_guard = self._template_fallback_guard(failure_class)
        if not fallback_guard.get("allowed", False):
            if self._blind_benchmark_policy():
                self.run["generic_template_fallback_blocked"] = True
                self.run["generic_template_fallback_block_reason"] = str(fallback_guard.get("why", "") or "")
                self.run["policy_block_detected"] = True
                self._note_failure_signature("blind_benchmark_generic_template_fallback_blocked")
                self._append_event(
                    step_id=None,
                    agent="RecoveryAgent",
                    event_type="POLICY_BLOCK",
                    severity="error",
                    payload={
                        "run_id": self.run.get("run_uid", ""),
                        "action": "generic_template_fallback_blocked",
                        "details": fallback_guard,
                    },
                )
                raise RuntimeError(
                    f"Generic template fallback is disabled in {self._benchmark_policy()} mode. "
                    "This run is invalid for blind benchmark reporting if it requires deterministic template rescue."
                )
            return None, "template_fallback_blocked", fallback_guard

        contract = self.run.get("plan_contract", {}) if isinstance(self.run.get("plan_contract", {}), dict) else {}
        gtf_path, fasta_path, ref_reason = _resolve_reference_paths_for_template_fallback(
            str(self.run.get("user_request", "")),
            data_root=self.cfg.data_root,
            selected_dir=self.cfg.selected_dir,
            official_benchmark_policy=self._blind_benchmark_policy(),
        )
        control_tag, treatment_tag = _extract_sample_tags_from_plan(self.run.get("plan", {}))
        req_control_tags, req_treatment_tags = _extract_group_tags_from_request_text(str(self.run.get("user_request", "")))
        if req_control_tags:
            control_tag = ",".join(req_control_tags)
        if req_treatment_tags:
            treatment_tag = ",".join(req_treatment_tags)
        subset_mode = True
        if "full data" in str(self.run.get("user_request", "")).lower():
            subset_mode = False

        preference_profile = self._active_preference_profile()
        provenance_mode = self._fallback_provenance_mode(contract)
        cache_paths = {"star_index_cache_root": "outputs/_cache/star_indexes"}
        excluded_pipeline_ids = {
            str(item).strip()
            for item in self.run.get("excluded_fallback_pipeline_ids", [])
            if str(item).strip()
        }
        previous_selection = self.run.get("fallback_selection", {})
        if isinstance(previous_selection, dict):
            previous_pipeline_id = str(previous_selection.get("selected_pipeline_id", "") or "").strip()
            if previous_pipeline_id:
                excluded_pipeline_ids.add(previous_pipeline_id)
        candidate, selection = select_ranked_fallback_plan(
            contract=contract,
            prompt=str(self.run.get("user_request", "")),
            data_root=str(self.cfg.data_root),
            selected_dir=str(self.cfg.selected_dir),
            reference_fasta=str(fasta_path),
            annotation_gtf=str(gtf_path),
            control_tag=str(control_tag or "S1"),
            treatment_tag=str(treatment_tag or "S6"),
            subset_mode=subset_mode,
            test_reads_per_fastq=1_000_000,
            cache_paths=cache_paths,
            graph_store=self.path_graph,
            preference_profile=preference_profile,
            provenance_mode=provenance_mode,
            excluded_pipeline_ids=sorted(excluded_pipeline_ids),
        )
        self.run["excluded_fallback_pipeline_ids"] = sorted(excluded_pipeline_ids)
        selected_pipeline_id = selection.get("selection", {}).get("pipeline_id", "") if isinstance(selection, dict) else ""
        self.run["fallback_selection"] = {
            "selected_pipeline_id": selected_pipeline_id,
            "excluded_pipeline_ids": sorted(excluded_pipeline_ids),
            "preference_profile": preference_profile,
            **selection,
        }
        self.run["fallback_catalog_size"] = int(selection.get("catalog_size", self.run.get("fallback_catalog_size", 0)))
        if selection.get("catalog_summary"):
            self.run["fallback_catalog_summary"] = selection.get("catalog_summary", [])
        if candidate is None:
            return None, "template_not_applicable", {
                "why": "no_ranked_fallback_selected",
                "selection": selection,
                "reference_resolution_reason": ref_reason,
            }

        action = f"template_{selection.get('selection', {}).get('pipeline_id', 'fallback')}"
        validation = {"passed": True, "missing_capabilities": [], "missing_tool_hints": []}
        composition: dict[str, Any] = {"attempted": False, "applied": False, "why": "contract_not_required"}
        if not _is_empty_contract(contract):
            validation = self._assess_repair_contract_for_plan(candidate, contract)
            if not validation.get("passed", False):
                composed_plan, composition = self._compose_contract_template_plan(
                    base_plan=candidate,
                    base_selection=selection,
                    contract=contract,
                    prompt=str(self.run.get("user_request", "")),
                    data_root=str(self.cfg.data_root),
                    selected_dir=str(self.cfg.selected_dir),
                    reference_fasta=str(fasta_path),
                    annotation_gtf=str(gtf_path),
                    control_tag=str(control_tag or "S1"),
                    treatment_tag=str(treatment_tag or "S6"),
                    subset_mode=subset_mode,
                    test_reads_per_fastq=1_000_000,
                    cache_paths=cache_paths,
                    preference_profile=preference_profile,
                )
                if composition.get("applied", False):
                    candidate = composed_plan
                    action = "template_composed_fallback"
                    validation = self._assess_repair_contract_for_plan(candidate, contract)
            if failure_class == "contract_mismatch" and not validation.get("passed", False):
                return None, "template_contract_failed", {
                    "why": "template_plan_failed_contract_validation",
                    "contract_validation": validation,
                    "selection": selection,
                    "composition": composition,
                }

        return candidate, action, {
            "why": "ranked_fallback_template_selected",
            "failure_class": failure_class,
            "selected_pipeline_id": selection.get("selection", {}).get("pipeline_id", ""),
            "contract_validation": validation,
            "composition": composition,
            "selection": selection,
            "reference_resolution_reason": ref_reason,
            "resolved_gtf": gtf_path,
            "resolved_fasta": fasta_path,
            "control_tag": str(control_tag or "S1"),
            "treatment_tag": str(treatment_tag or "S6"),
            "subset_mode": bool(subset_mode),
            "provenance_mode": provenance_mode,
        }
