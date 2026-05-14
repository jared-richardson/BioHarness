from __future__ import annotations

import re

from bio_harness.core.bash_placeholder_resolution import resolve_bash_placeholders
from bio_harness.core.failure_signatures import route_runtime_failure_signature
from bio_harness.core.wrapper_contracts import normalize_snpeff_codon_table
from bio_harness.harness.plan_semantic_guards import (
    repair_ambiguous_bcftools_expression_bash_run_commands,
    repair_invalid_bcftools_isec_bash_run_commands,
    repair_invalid_bcftools_view_bash_run_commands,
)
from scripts.run_agent_e2e_support import (
    Any,
    Path,
    _is_empty_contract,
    _json_dumps_safe,
    _apply_featurecounts_paired_mode,
    _apply_repaired_plan_with_resume,
    _failed_tool_name,
    _first_failed_step_number,
    _normalize_steps,
    _renumber_plan_steps,
    _repair_quantification_count_exports,
    _repair_scope_for_run,
    _repair_shared_variant_csv_exports_with_analysis_spec,
    assess_plan_contract,
    detect_plan_artifact_failure_signatures,
    shlex,
)


def _request_mentions_explicit_genome_size(user_request: str) -> bool:
    """Return whether the request text already specifies a genome-size estimate."""

    text = str(user_request or "").lower()
    if "genome size" in text:
        return True
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:k|kb|m|mb|g|gb)\b", text))


def _repair_flye_resource_settings(
    plan: dict[str, Any],
    *,
    failed_step_number: int,
    user_request: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lower Flye resource settings after an OOM-style assembly failure."""

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    target_idx = -1
    if failed_step_number > 0:
        idx = failed_step_number - 1
        if 0 <= idx < len(steps) and str(steps[idx].get("tool_name", "")).strip().lower() == "flye_assemble":
            target_idx = idx
    if target_idx < 0:
        for idx in range(len(steps) - 1, -1, -1):
            if str(steps[idx].get("tool_name", "")).strip().lower() == "flye_assemble":
                target_idx = idx
                break
    if target_idx < 0:
        return plan, {"changed": False, "why": "flye_step_not_found"}

    patched_steps = list(steps)
    target_step = dict(patched_steps[target_idx])
    args = dict(target_step.get("arguments", {})) if isinstance(target_step.get("arguments", {}), dict) else {}
    changed_fields: dict[str, dict[str, Any]] = {}

    raw_threads = args.get("threads", 0)
    try:
        current_threads = int(raw_threads or 0)
    except (TypeError, ValueError):
        current_threads = 0
    if current_threads != 1:
        args["threads"] = 1
        changed_fields["threads"] = {"before": raw_threads, "after": 1}

    genome_size = str(args.get("genome_size", "") or "").strip()
    explicit_size = _request_mentions_explicit_genome_size(user_request)
    if not explicit_size:
        target_genome_size = "100k" if bool(args.get("meta_mode", False)) else "500k"
        if genome_size.lower() != target_genome_size.lower():
            args["genome_size"] = target_genome_size
            changed_fields["genome_size"] = {
                "before": genome_size,
                "after": target_genome_size,
            }

    if not changed_fields:
        return plan, {"changed": False, "why": "flye_resource_settings_already_conservative"}

    target_step["arguments"] = args
    patched_steps[target_idx] = target_step
    patched_plan = dict(plan) if isinstance(plan, dict) else {}
    patched_plan["plan"] = patched_steps
    patched_plan = _renumber_plan_steps(patched_plan)
    return patched_plan, {
        "changed": True,
        "target_step_index": int(target_idx),
        "target_tool": "flye_assemble",
        "diff_summary": {"changed_fields": sorted(changed_fields.keys())},
        "changed_fields": changed_fields,
    }


class AgentE2ERuntimeRepairSupportMixin:
    def _apply_bash_placeholder_signature_repair(self) -> tuple[bool, dict[str, Any]]:
        """Resolve supported template placeholders in a failed ``bash_run``."""

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        steps = _normalize_steps(plan)
        if not steps:
            return False, {"why": "plan_missing"}

        failed_step_num = _first_failed_step_number(
            self.run.get("step_statuses", []),
            fallback_next_idx=int(self.run.get("next_step_idx", 0)),
        )
        target_idx = failed_step_num - 1
        if target_idx < 0 or target_idx >= len(steps):
            return False, {"why": "failed_step_not_found"}

        target_step = steps[target_idx]
        tool_name = str(target_step.get("tool_name", "") or "").strip().lower()
        if tool_name != "bash_run":
            return False, {"why": "failed_step_not_bash_run"}
        args = target_step.get("arguments", {}) if isinstance(target_step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "")
        route = route_runtime_failure_signature(
            command=command,
            error_text=str(self.run.get("error", "") or ""),
            tool_name=tool_name,
            issues=list(self.run.get("failure_signatures", [])),
        )
        if route != "unresolved_placeholder_in_command":
            return False, {"why": "signature_not_matched"}

        prior_step_arguments = [
            dict(step.get("arguments", {}))
            for step in steps[:target_idx]
            if isinstance(step.get("arguments", {}), dict)
        ]
        resolution = resolve_bash_placeholders(
            command,
            prior_step_arguments=prior_step_arguments,
            path_graph=self.path_graph,
            wrapper_parameter_defaults={
                "cwd": str(self.cfg.selected_dir),
                "output_dir": str(self.cfg.selected_dir),
                "results_dir": str(self.cfg.selected_dir),
                "selected_dir": str(self.cfg.selected_dir),
            },
            selected_dir=str(self.cfg.selected_dir),
        )
        if resolution.unresolved:
            return False, {
                "why": "placeholder_resolution_incomplete",
                "unresolved": list(resolution.unresolved),
            }
        if resolution.resolved_command == command:
            return False, {"why": "placeholder_resolution_not_changed"}

        patched_steps = list(steps)
        patched_step = dict(target_step)
        patched_args = dict(args)
        patched_args["command"] = resolution.resolved_command
        patched_step["arguments"] = patched_args
        patched_steps[target_idx] = patched_step
        patched_plan = dict(plan)
        patched_plan["plan"] = patched_steps
        patched_plan = _renumber_plan_steps(patched_plan)
        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "signature_guided_bash_placeholder_resolution",
            "target_step_index": int(target_idx),
            "target_tool": "bash_run",
            "resolutions": list(resolution.resolutions),
            "diff_summary": {
                "resolved_placeholder_count": len(resolution.resolutions),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }

    def _apply_snpeff_codon_table_signature_repair(self) -> tuple[bool, dict[str, Any]]:
        """Clear incompatible SnpEff codon-table overrides after runtime failure."""

        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        if not any(
            signature == "snpeff_invalid_codon_table"
            or signature.startswith("snpeff_invalid_codon_table:")
            for signature in signatures
        ):
            return False, {"why": "signature_not_matched"}

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        steps = _normalize_steps(plan)
        if not steps:
            return False, {"why": "plan_missing"}

        failed_step_num = _first_failed_step_number(
            self.run.get("step_statuses", []),
            fallback_next_idx=int(self.run.get("next_step_idx", 0)),
        )
        target_idx = -1
        if failed_step_num > 0:
            idx = failed_step_num - 1
            if 0 <= idx < len(steps) and str(steps[idx].get("tool_name", "")).strip().lower() == "snpeff_annotate":
                target_idx = idx
        if target_idx < 0:
            for idx in range(len(steps) - 1, -1, -1):
                if str(steps[idx].get("tool_name", "")).strip().lower() == "snpeff_annotate":
                    target_idx = idx
                    break
        if target_idx < 0:
            return False, {"why": "snpeff_step_not_found"}

        target_step = dict(steps[target_idx])
        args = dict(target_step.get("arguments", {})) if isinstance(target_step.get("arguments", {}), dict) else {}
        raw_codon_table = str(args.get("codon_table", "") or "").strip()
        if not raw_codon_table:
            return False, {"why": "codon_table_not_present"}

        normalized_codon_table = normalize_snpeff_codon_table(raw_codon_table)
        if normalized_codon_table == raw_codon_table:
            return False, {"why": "codon_table_already_normalized"}

        args["codon_table"] = normalized_codon_table
        target_step["arguments"] = args
        patched_steps = list(steps)
        patched_steps[target_idx] = target_step
        patched_plan = dict(plan)
        patched_plan["plan"] = patched_steps
        patched_plan = _renumber_plan_steps(patched_plan)
        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "signature_guided_snpeff_codon_table_repair",
            "signature_hits": sorted(signatures),
            "target_step_index": int(target_idx),
            "target_tool": "snpeff_annotate",
            "diff_summary": {
                "changed_fields": ["codon_table"],
                "before": raw_codon_table,
                "after": normalized_codon_table,
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }

    def _assess_repair_contract_for_plan(
        self,
        plan: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        """Assess a runtime-generated plan with the harness' richest validator."""

        if hasattr(self, "_assess_contract_for_plan"):
            return self._assess_contract_for_plan(plan, contract)
        if _is_empty_contract(contract):
            return {
                "passed": True,
                "missing_capabilities": [],
                "missing_required_tool_hints": [],
                "missing_tool_hints": [],
            }
        return assess_plan_contract(
            plan,
            contract,
            capability_specs=self.capability_specs,
        )

    def _replan_prompt_for_failure(
        self,
        failure_class: str,
        reason: str,
        *,
        focus_mode: str = "step_local",
        attempt_num: int = 1,
    ) -> str:
        stderr_tail = ""
        try:
            stderr_text = Path(self.run["run_files"]["stderr"]).read_text(encoding="utf-8")
            stderr_tail = stderr_text[-12000:]
        except Exception:
            stderr_tail = ""
        failed_step_number = _first_failed_step_number(
            self.run.get("step_statuses", []),
            fallback_next_idx=int(self.run.get("next_step_idx", 0)),
        )
        failed_tool = _failed_tool_name(self.run.get("plan", {}), failed_step_number)
        repair_context = self._build_repair_prompt_context(
            failure_class=failure_class,
            reason=reason,
            focus_mode=focus_mode,
        )
        return (
            "You are repairing a failed executable plan.\n"
            "Return ONLY executable JSON with `thought_process` and `plan`.\n"
            "Use only supported tools and runnable commands.\n\n"
            f"Original user request:\n{self.run.get('user_request', '')}\n\n"
            f"Repair attempt: {attempt_num}\n"
            f"Repair focus mode: {focus_mode}\n"
            f"Failure class: {failure_class}\n"
            f"Failure reason: {reason}\n\n"
            f"Failed step number: {failed_step_number}\n"
            f"Failed tool: {failed_tool}\n\n"
            "Repair constraints:\n"
            "- Keep changes minimal and local to the failure.\n"
            "- Do NOT replay successful expensive alignment/QC steps unless the failure is in those steps.\n"
            "- Prefer patching metadata/counts/script-path issues with lightweight steps.\n\n"
            f"Focused repair context:\n{_json_dumps_safe(repair_context, indent=2)}\n\n"
            f"Contract requirements (must satisfy):\n{_json_dumps_safe(self.run.get('plan_contract', {}), indent=2)}\n\n"
            f"Previous plan:\n{_json_dumps_safe(self.run.get('plan', {}), indent=2)}\n\n"
            f"Recent stderr tail:\n{stderr_tail}\n"
        )

    def _build_metadata_from_counts_command(self, counts_matrix: str, metadata_table: str) -> str:
        counts_q = shlex.quote(str(counts_matrix))
        metadata_q = shlex.quote(str(metadata_table))
        awk_script = (
            'BEGIN{FS="\\t"; OFS="\\t"} '
            'NR==1 {'
            'print "sample","condition"; '
            'for(i=7;i<=NF;i++){'
            's=$i; gsub(/^.*\\//,"",s); '
            'sl=tolower(s); c="unknown"; '
            'if (sl ~ /(control|s1)/) c="control"; '
            'else if (sl ~ /(treat|treatment|case|s6)/) c="treatment"; '
            'print s,c'
            '} '
            'exit'
            '}'
        )
        return f"awk '{awk_script}' {counts_q} > {metadata_q}"

    def _augment_failure_signatures_from_artifacts(self) -> list[str]:
        detected = detect_plan_artifact_failure_signatures(
            run=self.run,
            selected_dir=self.cfg.selected_dir,
        )
        for signature in detected:
            self._note_failure_signature(signature)
        return detected

    def _repair_scope_summary(self) -> dict[str, Any]:
        return _repair_scope_for_run(self.run)

    def _template_fallback_guard(self, failure_class: str) -> dict[str, Any]:
        scope = self._repair_scope_summary()
        if self._direct_skill_smoke_run():
            return {
                "allowed": False,
                "why": "direct_skill_smoke_disables_generic_template_fallback",
                "repair_scope": scope,
                "failure_class": failure_class,
            }
        if self._blind_benchmark_policy():
            return {
                "allowed": False,
                "why": f"{self._benchmark_policy()}_disables_generic_template_fallback",
                "repair_scope": scope,
                "failure_class": failure_class,
                "benchmark_policy": self._benchmark_policy(),
            }
        if scope.get("provenance_locked", False) and scope.get("scope") in {"step_local", "tail_local", "subgraph_local"}:
            return {
                "allowed": False,
                "why": "provenance_locked_local_repair_required",
                "repair_scope": scope,
                "failure_class": failure_class,
            }
        return {
            "allowed": True,
            "why": "fallback_allowed",
            "repair_scope": scope,
            "failure_class": failure_class,
        }

    def _runtime_plan_mutation_repair_guard(self, failure_class: str) -> dict[str, Any]:
        """Block deterministic runtime plan rewrites in strict benchmark mode."""

        scope = self._repair_scope_summary()
        if self._planning_strict_benchmark_policy() or self._blind_benchmark_policy():
            return {
                "allowed": False,
                "why": f"{self._benchmark_policy()}_disables_runtime_plan_mutation_repairs",
                "repair_scope": scope,
                "failure_class": failure_class,
                "benchmark_policy": self._benchmark_policy(),
            }
        return {
            "allowed": True,
            "why": "runtime_plan_mutation_repairs_allowed",
            "repair_scope": scope,
            "failure_class": failure_class,
        }

    def _apply_output_adapter_tail_repair(self) -> tuple[bool, dict[str, Any]]:
        scope = self._repair_scope_summary()
        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        if scope.get("scope") not in {"step_local", "tail_local"}:
            return False, {"why": "repair_scope_not_local_tail", "repair_scope": scope}
        if str(scope.get("failed_tool", "")).strip().lower() != "bash_run":
            return False, {"why": "failed_step_not_bash_run", "repair_scope": scope}
        if not bool(scope.get("failed_outputs_csv", False)):
            return False, {"why": "failed_step_not_csv_export", "repair_scope": scope}

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        analysis_spec = self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {}
        adapters = [
            ("shared_variant_csv_export", lambda current: _repair_shared_variant_csv_exports_with_analysis_spec(current, analysis_spec=analysis_spec)),
            ("quantification_count_export", _repair_quantification_count_exports),
        ]
        for adapter_name, adapter in adapters:
            patched_plan, meta = adapter(plan)
            if not meta.get("changed", False):
                continue
            resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
            return True, {
                "why": "typed_output_adapter_repair",
                "adapter": adapter_name,
                "repair_scope": scope,
                "signature_hits": sorted(signatures),
                "diff_summary": {
                    **meta.get("diff_summary", {}),
                    "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                    "resume_idx": resume_meta.get("resume_idx", 0),
                },
                "resume": resume_meta,
                "repair_meta": meta,
            }

        return False, {"why": "no_output_adapter_matched", "repair_scope": scope}

    def _apply_bcftools_expression_signature_repair(self) -> tuple[bool, dict[str, Any]]:
        """Repair low-ambiguity ambiguous ``bcftools`` expression namespaces."""

        self._augment_failure_signatures_from_artifacts()
        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        if not any(
            signature == "bcftools_ambiguous_expression_namespace"
            or signature.startswith("bcftools_ambiguous_expression_namespace:")
            or signature == "bcftools_missing_expression_namespace_field"
            or signature.startswith("bcftools_missing_expression_namespace_field:")
            for signature in signatures
        ):
            return False, {"why": "signature_not_matched"}

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        patched_plan, meta = repair_ambiguous_bcftools_expression_bash_run_commands(
            plan,
            cwd=self.cfg.selected_dir,
        )
        if not meta.get("changed", False):
            return False, {"why": meta.get("why", "bcftools_expression_repair_not_changed")}

        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "signature_guided_bcftools_expression_namespace_repair",
            "signature_hits": sorted(signatures),
            "repair_meta": meta,
            "diff_summary": {
                **meta.get("diff_summary", {}),
                "repair_count": len(meta.get("repairs", [])),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }

    def _apply_bcftools_view_cli_repair(self) -> tuple[bool, dict[str, Any]]:
        """Repair deterministic malformed ``bcftools view`` option/value usage."""

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        patched_plan, meta = repair_invalid_bcftools_view_bash_run_commands(plan)
        if not meta.get("changed", False):
            return False, {"why": meta.get("why", "bcftools_view_cli_repair_not_changed")}

        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "deterministic_bcftools_view_cli_repair",
            "repair_meta": meta,
            "diff_summary": {
                **meta.get("diff_summary", {}),
                "repair_count": len(meta.get("repairs", [])),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }

    def _apply_bcftools_isec_output_repair(self) -> tuple[bool, dict[str, Any]]:
        """Repair deterministic ``bcftools isec -p`` export misuse."""

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        steps = _normalize_steps(plan)
        failed_step_num = _first_failed_step_number(
            self.run.get("step_statuses", []),
            fallback_next_idx=int(self.run.get("next_step_idx", 0)),
        )
        target_idx = failed_step_num - 1
        if target_idx < 0 or target_idx >= len(steps):
            return False, {"why": "failed_step_not_found"}
        target_step = steps[target_idx]
        args = target_step.get("arguments", {}) if isinstance(target_step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "")
        if "bcftools isec" not in command.lower():
            return False, {"why": "failed_command_not_bcftools_isec"}
        patched_plan, meta = repair_invalid_bcftools_isec_bash_run_commands(plan)
        if not meta.get("changed", False):
            return False, {"why": meta.get("why", "bcftools_isec_output_repair_not_changed")}

        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "deterministic_bcftools_isec_output_repair",
            "repair_meta": meta,
            "diff_summary": {
                **meta.get("diff_summary", {}),
                "repair_count": len(meta.get("repairs", [])),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }

    def _apply_vcf_shared_export_signature_repair(self) -> tuple[bool, dict[str, Any]]:
        self._augment_failure_signatures_from_artifacts()
        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        relevant = {
            "vcf_filter_tag_missing_in_header",
            "snpeff_ann_semantics_mismatch",
            "shared_variant_export_shell_fragility",
        }
        if not signatures.intersection(relevant):
            return False, {"why": "signature_not_matched"}

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        patched_plan, meta = _repair_shared_variant_csv_exports_with_analysis_spec(
            plan,
            analysis_spec=self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {},
        )
        if not meta.get("changed", False):
            return False, {"why": meta.get("why", "shared_variant_export_not_changed")}

        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "signature_guided_shared_variant_export_repair",
            "signature_hits": sorted(signatures),
            "diff_summary": {
                **meta.get("diff_summary", {}),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
            "repair_meta": meta,
        }

    def _apply_featurecounts_paired_signature_repair(self) -> tuple[bool, dict[str, Any]]:
        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        if "featurecounts_paired_mode_required" not in signatures:
            return False, {"why": "signature_not_matched"}

        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        patched_plan, meta = _apply_featurecounts_paired_mode(plan, force=True)
        if not meta.get("changed", False):
            return False, {"why": meta.get("reason", "featurecounts_pairing_not_changed")}

        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "signature_guided_featurecounts_paired_mode",
            "signature_hits": sorted(signatures),
            "changed_steps": meta.get("changed_steps", []),
            "diff_summary": {
                **meta.get("diff_summary", {}),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }

    def _apply_deseq2_metadata_signature_repair(self) -> tuple[bool, dict[str, Any]]:
        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        if not signatures.intersection({"deseq2_metadata_mismatch", "deseq2_counts_parse_error"}):
            return False, {"why": "signature_not_matched"}

        plan = self.run.get("plan", {})
        steps = _normalize_steps(plan)
        if not steps:
            return False, {"why": "plan_missing"}

        failed_step_num = _first_failed_step_number(
            self.run.get("step_statuses", []),
            fallback_next_idx=int(self.run.get("next_step_idx", 0)),
        )
        target_idx = -1
        if failed_step_num > 0:
            idx = failed_step_num - 1
            if 0 <= idx < len(steps) and str(steps[idx].get("tool_name", "")).strip().lower() == "deseq2_run":
                target_idx = idx
        if target_idx < 0:
            for idx in range(len(steps) - 1, -1, -1):
                if str(steps[idx].get("tool_name", "")).strip().lower() == "deseq2_run":
                    target_idx = idx
                    break
        if target_idx < 0:
            return False, {"why": "deseq2_step_not_found"}

        deseq_step = steps[target_idx]
        args = deseq_step.get("arguments", {}) if isinstance(deseq_step.get("arguments", {}), dict) else {}
        counts_matrix = str(args.get("counts_matrix", "")).strip()
        metadata_table = str(args.get("metadata_table", "")).strip()
        if not counts_matrix or not metadata_table:
            return False, {"why": "deseq2_args_missing"}

        metadata_cmd = self._build_metadata_from_counts_command(counts_matrix, metadata_table)
        existing_prev = steps[target_idx - 1] if target_idx > 0 else {}
        prev_cmd = ""
        if isinstance(existing_prev, dict):
            prev_cmd = str((existing_prev.get("arguments") or {}).get("command", ""))
        if "print \"sample\",\"condition\"" in prev_cmd and counts_matrix in prev_cmd and metadata_table in prev_cmd:
            return False, {"why": "metadata_repair_already_present"}

        insert_step = {
            "tool_name": "bash_run",
            "arguments": {"command": metadata_cmd},
        }
        patched_steps = list(steps[:target_idx]) + [insert_step] + list(steps[target_idx:])
        patched_plan = dict(plan) if isinstance(plan, dict) else {}
        patched_plan["plan"] = patched_steps
        patched_plan = _renumber_plan_steps(patched_plan)

        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "signature_guided_metadata_regeneration",
            "signature_hits": sorted(signatures),
            "inserted_before_step": int(target_idx + 1),
            "diff_summary": {
                "before_step_count": len(steps),
                "after_step_count": len(patched_steps),
                "inserted_steps": 1,
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }

    def _apply_flye_resource_signature_repair(self) -> tuple[bool, dict[str, Any]]:
        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        relevant = {"runtime_out_of_memory", "flye_out_of_memory", "flye_zero_coverage_estimate"}
        if not signatures.intersection(relevant):
            return False, {"why": "signature_not_matched"}

        failed_step_num = _first_failed_step_number(
            self.run.get("step_statuses", []),
            fallback_next_idx=int(self.run.get("next_step_idx", 0)),
        )
        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        patched_plan, meta = _repair_flye_resource_settings(
            plan,
            failed_step_number=failed_step_num,
            user_request=str(self.run.get("user_request", "") or ""),
        )
        if not meta.get("changed", False):
            return False, {"why": meta.get("why", "flye_resource_repair_not_changed")}

        resume_meta = _apply_repaired_plan_with_resume(self.run, patched_plan)
        return True, {
            "why": "signature_guided_flye_resource_repair",
            "signature_hits": sorted(signatures),
            "repair_meta": meta,
            "diff_summary": {
                **meta.get("diff_summary", {}),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        }
