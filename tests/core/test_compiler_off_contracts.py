from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.core.analysis_spec import deterministic_analysis_spec
from bio_harness.core.protocol_grounding import deterministic_protocol_repair


@pytest.fixture
def compiler_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.core.protocol_grounding._repair.TEMPLATE_COMPILER_TYPES",
        frozenset(),
    )


def test_compiler_off_fastq_deseq_drops_incompatible_direct_wrapper_lock(
    compiler_off: None,
) -> None:
    spec = deterministic_analysis_spec(
        (
            "Use only deseq2_run to identify differentially expressed genes between "
            "planktonic and biofilm conditions."
        ),
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": "/tmp/deseq/SRR1278968_1.fastq"},
            {"name": "SRR1278968_2.fastq", "path": "/tmp/deseq/SRR1278968_2.fastq"},
            {"name": "sample_metadata.tsv", "path": "/tmp/deseq/sample_metadata.tsv"},
        ],
    )

    assert spec["explicit_execution_intent"] == {}
    assert spec["execution_contract"]["input_mode"] == "raw_fastq"
    assert spec["execution_contract"]["execution_mode"] == "compiled_pipeline"


def test_compiler_off_count_matrix_deseq_preserves_compatible_direct_wrapper_lock(
    compiler_off: None,
) -> None:
    spec = deterministic_analysis_spec(
        (
            "Use only the deseq2_run tool on /tmp/airway/counts.tsv with metadata "
            "/tmp/airway/meta.tsv and write outputs under /tmp/deseq_out."
        ),
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
    )

    assert spec["explicit_execution_intent"]["locked_tools"] == ["deseq2_run"]
    assert spec["execution_contract"]["input_mode"] == "count_matrix"
    assert spec["execution_contract"]["execution_mode"] == "direct_wrapper"
    assert spec["execution_contract"]["compatible_tools"] == ["deseq2_run"]


def test_compiler_off_protocol_repair_does_not_fabricate_fastq_deseq_pipeline(
    tmp_path: Path,
    compiler_off: None,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    analysis_spec = deterministic_analysis_spec(
        (
            "Identify differentially expressed genes between planktonic and biofilm "
            "conditions using DESeq2."
        ),
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": str(data_root / "SRR1278968_1.fastq")},
            {"name": "SRR1278968_2.fastq", "path": str(data_root / "SRR1278968_2.fastq")},
            {"name": "sample_metadata.tsv", "path": str(data_root / "sample_metadata.tsv")},
        ],
    )
    candidate_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "echo wrong"},
            }
        ]
    }

    repaired, meta = deterministic_protocol_repair(
        candidate_plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert repaired == candidate_plan
    assert meta["changed"] is False
    assert meta["why"] in {"no_protocol_grounding", "no_deterministic_protocol_repair"}
