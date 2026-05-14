from __future__ import annotations

from pathlib import Path

from bio_harness.agents.orchestrator import Orchestrator


def _orchestrator_stub() -> Orchestrator:
    return Orchestrator.__new__(Orchestrator)


def test_subagent_dataset_scout_discovers_pairs(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sampleA_R1_001.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "sampleA_R2_001.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "notes.txt").write_text("ignore\n", encoding="utf-8")

    orchestrator = _orchestrator_stub()
    result = orchestrator._subagent_dataset_scout(str(data_root))

    assert result["file_count"] == 2
    assert result["pairs"] == [
        {
            "sample": "sampleA",
            "R1": str(data_root / "sampleA_R1_001.fastq.gz"),
            "R2": str(data_root / "sampleA_R2_001.fastq.gz"),
        }
    ]


def test_subagent_dataset_scout_reports_missing_path():
    orchestrator = _orchestrator_stub()

    result = orchestrator._subagent_dataset_scout("/tmp/definitely_missing_bio_harness_path")

    assert result["file_count"] == 0
    assert "warning" in result


def test_subagent_requirements_combines_splicing_and_de():
    orchestrator = _orchestrator_stub()

    requirements = orchestrator._subagent_requirements("Run alternative splicing and differential expression with DESeq2")

    assert any("splicing tool" in requirement for requirement in requirements)
    assert any("count strategy" in requirement for requirement in requirements)


def test_infer_autonomy_mode_detects_proceed_language():
    orchestrator = _orchestrator_stub()

    assert orchestrator._infer_autonomy_mode("Proceed and infer the rest.") is True
    assert orchestrator._infer_autonomy_mode("Please analyze this carefully.") is False


def test_detect_context_completeness_flags_missing_splicing_context():
    orchestrator = _orchestrator_stub()

    result = orchestrator._detect_context_completeness(
        "Run alternative splicing analysis.",
        {"data_context": {"pairs": []}},
    )

    assert result["is_likely_complete"] is False
    assert "input sample pairing not confirmed" in result["missing"]
    assert "reference FASTA + GTF paths not confirmed in this turn" in result["missing"]
    assert "splicing tool not explicitly selected" in result["missing"]
    assert "aligner not explicitly selected" in result["missing"]


def test_detect_context_completeness_accepts_explicit_splicing_context():
    orchestrator = _orchestrator_stub()

    result = orchestrator._detect_context_completeness(
        "Run alternative splicing with rMATS and STAR using /tmp/ref.fa and /tmp/genes.gtf.",
        {
            "data_context": {
                "pairs": [
                    {"sample": "case1", "R1": "/tmp/case1_R1_001.fastq.gz", "R2": "/tmp/case1_R2_001.fastq.gz"}
                ]
            }
        },
    )

    assert result["is_likely_complete"] is True
    assert result["missing"] == []
