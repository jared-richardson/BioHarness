from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bio_harness.core.file_manifest import FileManifest
from scripts.run_agent_e2e_planner_settings import AgentE2EPlannerSettingsMixin
from scripts.run_agent_e2e_support import _json_dumps_safe


class _StubOrchestrator:
    def _available_skill_metadata(self) -> list[dict[str, object]]:
        return []


class _StubPlannerHarness(AgentE2EPlannerSettingsMixin):
    def __init__(self, *, prompt: str, run: dict[str, object], selected_dir: Path) -> None:
        self.cfg = SimpleNamespace(prompt=prompt, selected_dir=selected_dir)
        self.run = run
        self.orchestrator = _StubOrchestrator()


def test_json_dumps_safe_serializes_file_manifest() -> None:
    manifest = FileManifest.from_discovered_files(
        [{"path": "/tmp/example/sample.bam"}],
        analysis_type="transcript_quantification",
        output_dir="/tmp/output",
    )

    rendered = _json_dumps_safe({"file_manifest": manifest}, indent=2)

    assert '"entries"' in rendered
    assert 'sample.bam' in rendered
    assert 'output_dir' in rendered


def test_protocol_replan_prompt_handles_analysis_spec_with_file_manifest(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    manifest = FileManifest.from_discovered_files(
        [{"path": str(selected_dir / "reads.bam")}],
        analysis_type="transcript_quantification",
        output_dir=str(selected_dir / "out"),
    )
    harness = _StubPlannerHarness(
        prompt="Run stringtie_quant on the provided aligned BAM and annotation directly.",
        selected_dir=selected_dir,
        run={
            "plan": {
                "plan": [
                    {
                        "tool_name": "stringtie_quant",
                        "arguments": {
                            "input_bam": str(selected_dir / "reads.bam"),
                            "annotation_gtf": str(selected_dir / "genes.gtf"),
                            "output_gtf": str(selected_dir / "out" / "assembled.gtf"),
                        },
                        "step_id": 1,
                    }
                ]
            },
            "analysis_spec": {
                "analysis_type": "transcript_quantification",
                "chosen_method": "stringtie_quant",
                "preferred_tools": ["stringtie_quant"],
                "file_manifest": manifest,
                "protocol_grounding": {
                    "required_tools": ["stringtie_quant"],
                },
            },
            "plan_contract": {
                "must_include_capabilities": ["quantification", "reference_inputs"],
                "required_tool_hints": ["stringtie_quant"],
            },
            "step_statuses": [],
            "next_step_idx": 0,
            "failure_signatures": [],
        },
    )

    prompt = harness._protocol_replan_prompt(
        analysis_spec=harness.run["analysis_spec"],
        validation={"passed": False, "missing_required_tools": ["stringtie_quant"]},
        plan=harness.run["plan"],
    )

    assert "file_manifest" in prompt
    assert "stringtie_quant" in prompt
    assert "required_tools" in prompt


def test_contract_focus_prompt_includes_repair_context_and_selected_dir_rule(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    harness = _StubPlannerHarness(
        prompt="Identify shared variants in evolved bacterial isolates relative to an ancestor.",
        selected_dir=selected_dir,
        run={
            "plan": {
                "plan": [
                    {
                        "tool_name": "freebayes_call",
                        "arguments": {
                            "input_bam": str(selected_dir / "alignments" / "evol1.bam"),
                            "reference_fasta": str(selected_dir / "ancestor_assembly" / "scaffolds.fasta"),
                            "output_vcf": str(selected_dir / "evol1_call" / "evol1_raw.vcf"),
                        },
                        "step_id": 1,
                    }
                ]
            },
            "analysis_spec": {
                "analysis_type": "bacterial_evolution_variant_calling",
            },
            "plan_contract": {
                "must_include_capabilities": ["variant_calling", "annotation"],
                "required_tool_hints": ["freebayes_call", "snpeff_annotate"],
            },
            "step_statuses": ["failed"],
            "next_step_idx": 0,
            "failure_signatures": [],
        },
    )

    prompt = harness._planner_contract_focus_prompt(
        contract=harness.run["plan_contract"],
        latest_validation={
            "passed": False,
            "artifact_role_issues": [
                (
                    "snpeff_annotate.input_vcf:input_in_selected_dir_without_producer:"
                    f"{selected_dir / 'evol1' / 'freebayes_evol1_filtered_annotated.vcf.gz'}"
                )
            ],
        },
        prior_plan=harness.run["plan"],
    )

    assert "Focused repair context:" in prompt
    assert "Every selected-dir input must be produced by an earlier step" in prompt
    assert "selected_dir_producer_hints" in prompt
