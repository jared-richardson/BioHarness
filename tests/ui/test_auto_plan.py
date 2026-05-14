from __future__ import annotations

import pytest

from bio_harness.ui.auto_plan import (
    is_actionable_execution_plan,
    normalize_ui_auto_plan,
)


class _FakeOrchestrator:
    def __init__(self, analysis_spec: dict[str, object]) -> None:
        self.analysis_spec = analysis_spec
        self.calls: list[dict[str, object]] = []

    def build_analysis_spec(
        self,
        user_query: str,
        contract: dict[str, object] | None = None,
        *,
        selected_dir: str | None = None,
        data_root: str | None = None,
        project_root: str | None = None,
        benchmark_policy: str = "scientific_harness",
    ) -> dict[str, object]:
        self.calls.append(
            {
                "user_query": user_query,
                "contract": contract or {},
                "selected_dir": selected_dir or "",
                "data_root": data_root or "",
                "project_root": project_root or "",
                "benchmark_policy": benchmark_policy,
            }
        )
        return dict(self.analysis_spec)


def test_is_actionable_execution_plan_accepts_typed_wrappers() -> None:
    plan = {
        "plan": [
            {"tool_name": "salmon_quant", "arguments": {"reads_1": "r1.fq.gz"}},
        ]
    }

    assert is_actionable_execution_plan(plan) is True


def test_is_actionable_execution_plan_ignores_probe_only_bash() -> None:
    plan = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "which salmon"}},
        ]
    }

    assert is_actionable_execution_plan(plan) is False


def test_is_actionable_execution_plan_ignores_output_free_read_only_bash() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "cd /tmp/run && "
                        "bcftools view -h variants.vcf.gz | grep INFO && "
                        "bcftools view -H variants.vcf.gz | head -n 10"
                    )
                },
            },
        ]
    }

    assert is_actionable_execution_plan(plan) is False


def test_normalize_ui_auto_plan_uses_repaired_wrapper_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    original_plan = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "salmon quant ..."}},
            {"tool_name": "bash_run", "arguments": {"command": "awk ... > final/transcript_counts.tsv"}},
        ]
    }
    repaired_plan = {
        "plan": [
            {"tool_name": "salmon_quant", "arguments": {"reads_1": "reads_1.fq.gz", "reads_2": "reads_2.fq.gz"}},
        ]
    }
    fake = _FakeOrchestrator({"analysis_type": "transcript_quantification", "protocol_grounding": {"required_tools": ["salmon_quant"]}})

    monkeypatch.setattr(
        "bio_harness.ui.auto_plan.deterministic_protocol_repair",
        lambda *args, **kwargs: (repaired_plan, {"changed": True, "why": "full_template_replacement"}),
    )

    selected_plan, meta = normalize_ui_auto_plan(
        original_plan,
        orchestrator=fake,
        user_request="Quantify transcripts for these paired-end reads.",
        contract={"must_include_capabilities": ["quantification"]},
        selected_dir="/tmp/selected",
        data_root="/tmp/data",
        project_root="/tmp/project",
    )

    assert selected_plan == repaired_plan
    assert meta["actionable"] is True
    assert meta["repair_meta"]["changed"] is True
    assert meta["protocol_validation"]["passed"] is True
    assert meta["semantic_validation"]["passed"] is True
    assert meta["selected_tools"] == ["salmon_quant"]
    assert fake.calls[0]["selected_dir"] == "/tmp/selected"
    assert fake.calls[0]["benchmark_policy"] == "scientific_harness"


def test_normalize_ui_auto_plan_keeps_original_plan_if_repair_is_not_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_plan = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "salmon quant -l A ..."}},
        ]
    }
    fake = _FakeOrchestrator({"analysis_type": "transcript_quantification", "protocol_grounding": {}})

    monkeypatch.setattr(
        "bio_harness.ui.auto_plan.deterministic_protocol_repair",
        lambda *args, **kwargs: ({"plan": []}, {"changed": True, "why": "bad_repair"}),
    )

    selected_plan, meta = normalize_ui_auto_plan(
        original_plan,
        orchestrator=fake,
        user_request="Quantify transcripts for these paired-end reads.",
        contract={},
        selected_dir="/tmp/selected",
        data_root="/tmp/data",
        project_root="/tmp/project",
    )

    assert selected_plan == original_plan
    assert meta["actionable"] is True
    assert meta["selected_tools"] == ["bash_run"]


def test_normalize_ui_auto_plan_prefers_compiled_template_when_repaired_plan_fails_grounding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_plan = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "which python mafft iqtree2"}},
            {"tool_name": "fastqc_run", "arguments": {"input_file": "sequences.fasta", "output_dir": "/tmp/out"}},
        ]
    }
    repaired_plan = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "which python mafft iqtree2"}},
            {"tool_name": "fastqc_run", "arguments": {"input_file": "sequences.fasta", "output_dir": "/tmp/out"}},
            {"tool_name": "phylogenetics_iqtree_style", "arguments": {"alignment_fasta": "aligned.fasta"}},
        ]
    }
    compiled_template = {
        "plan": [
            {"tool_name": "mafft_align", "arguments": {"input_fasta": "sequences.fasta"}},
            {"tool_name": "phylogenetics_iqtree_style", "arguments": {"alignment_fasta": "aligned.fasta"}},
        ]
    }
    fake = _FakeOrchestrator(
        {
            "analysis_type": "phylogenetics",
            "protocol_grounding": {"required_tools": ["mafft_align", "phylogenetics_iqtree_style"]},
        }
    )

    monkeypatch.setattr(
        "bio_harness.ui.auto_plan.deterministic_protocol_repair",
        lambda *args, **kwargs: (
            repaired_plan,
            {
                "changed": True,
                "why": "deterministic_protocol_repair_applied",
                "_full_template": compiled_template,
            },
        ),
    )

    def _fake_protocol_validation(plan: dict[str, object], _analysis_spec: dict[str, object]) -> dict[str, object]:
        tools = [str(step.get("tool_name", "")) for step in plan.get("plan", []) if isinstance(step, dict)]
        return {"passed": tools == ["mafft_align", "phylogenetics_iqtree_style"]}

    def _fake_semantic_validation(plan: dict[str, object], *, analysis_spec: dict[str, object]) -> dict[str, object]:
        del analysis_spec
        tools = [str(step.get("tool_name", "")) for step in plan.get("plan", []) if isinstance(step, dict)]
        return {"passed": tools == ["mafft_align", "phylogenetics_iqtree_style"]}

    monkeypatch.setattr("bio_harness.ui.auto_plan.assess_protocol_grounding", _fake_protocol_validation)
    monkeypatch.setattr("bio_harness.ui.auto_plan._assess_plan_semantic_guards", _fake_semantic_validation)

    selected_plan, meta = normalize_ui_auto_plan(
        original_plan,
        orchestrator=fake,
        user_request="Build a phylogenetic tree from the provided FASTA.",
        contract={},
        selected_dir="/tmp/selected",
        data_root="/tmp/data",
        project_root="/tmp/project",
    )

    assert selected_plan == compiled_template
    assert meta["selected_source"] == "compiled_template"
    assert meta["selected_tools"] == ["mafft_align", "phylogenetics_iqtree_style"]


def test_normalize_ui_auto_plan_forwards_benchmark_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {
        "plan": [
            {"tool_name": "salmon_quant", "arguments": {"reads_1": "reads_1.fq.gz", "reads_2": "reads_2.fq.gz"}},
        ]
    }
    fake = _FakeOrchestrator({"analysis_type": "transcript_quantification"})

    monkeypatch.setattr(
        "bio_harness.ui.auto_plan.deterministic_protocol_repair",
        lambda *args, **kwargs: (plan, {"changed": False, "why": "already_typed"}),
    )

    selected_plan, meta = normalize_ui_auto_plan(
        plan,
        orchestrator=fake,
        user_request="Quantify transcripts in blind benchmark mode.",
        contract={},
        selected_dir="/tmp/selected",
        data_root="/tmp/data",
        project_root="/tmp/project",
        benchmark_policy="official_bioagentbench",
    )

    assert selected_plan == plan
    assert meta["benchmark_policy"] == "official_bioagentbench"
    assert fake.calls[0]["benchmark_policy"] == "official_bioagentbench"


def test_normalize_ui_auto_plan_preserves_original_plan_when_repair_breaks_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_plan = {
        "plan": [
            {
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": "/tmp/sample_R1.fastq.gz",
                    "r2": "/tmp/sample_R2.fastq.gz",
                    "gtf": "/tmp/annotation.gtf",
                    "reference": "/tmp/reference.fa",
                    "output_dir": "/tmp/out",
                },
            }
        ]
    }
    degraded_plan = {
        "plan": [
            {
                "tool_name": "scanpy_workflow",
                "arguments": {
                    "input_path": "/tmp/adata.h5ad",
                    "output_dir": "/tmp/out",
                },
            }
        ]
    }
    fake = _FakeOrchestrator({"analysis_type": "single_cell_rna_seq", "protocol_grounding": {}})

    monkeypatch.setattr(
        "bio_harness.ui.auto_plan.deterministic_protocol_repair",
        lambda *args, **kwargs: (
            degraded_plan,
            {"changed": True, "why": "deterministic_protocol_repair_applied"},
        ),
    )

    selected_plan, meta = normalize_ui_auto_plan(
        original_plan,
        orchestrator=fake,
        user_request="Run the single-cell benchmark task.",
        contract={
            "must_include_capabilities": [
                "alignment",
                "single_cell_analysis",
                "reference_inputs",
            ]
        },
        selected_dir="/tmp/selected",
        data_root="/tmp/data",
        project_root="/tmp/project",
        benchmark_policy="official_bioagentbench",
    )

    assert selected_plan == original_plan
    assert meta["selected_source"] == "original_plan_contract_preserved"
    assert meta["contract_validation"]["passed"] is True
