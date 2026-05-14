from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from bio_harness.agents.orchestrator import Orchestrator


def _orchestrator_stub() -> Orchestrator:
    return Orchestrator.__new__(Orchestrator)


def test_manual_hint_returns_first_available_output(monkeypatch):
    orchestrator = _orchestrator_stub()

    def fake_run(cmd, capture_output, text, timeout):  # noqa: ARG001
        if cmd == ["samtools", "--help"]:
            return SimpleNamespace(stdout="samtools help text", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("bio_harness.agents.orchestrator_validation_helpers.subprocess.run", fake_run)

    assert orchestrator._manual_hint("samtools") == "samtools help text"


def test_manual_hint_returns_empty_when_all_probes_fail(monkeypatch):
    orchestrator = _orchestrator_stub()

    def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("nope")

    monkeypatch.setattr("bio_harness.agents.orchestrator_validation_helpers.subprocess.run", boom)

    assert orchestrator._manual_hint("samtools") == ""


def test_regenerate_splicing_lists_rewrites_control_and_treatment_lists(tmp_path: Path):
    orchestrator = _orchestrator_stub()
    splicing_dir = tmp_path / "outputs" / "splicing_auto"
    splicing_dir.mkdir(parents=True)
    manifest = splicing_dir / "fastq_manifest.txt"
    manifest.write_text(
        "\n".join(
            [
                str(tmp_path / "A_S1_L001_R1_001.fastq.gz"),
                str(tmp_path / "B_S6_L001_R1_001.fastq.gz"),
                str(tmp_path / "ignore_S2_L001_R1_001.fastq.gz"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = orchestrator._regenerate_splicing_lists(str(tmp_path))

    assert result["ok"] is True
    assert result["control_count"] == 1
    assert result["treatment_count"] == 1
    assert "A_S1_L001_R1_001.fastq.gz" in (splicing_dir / "control_r1.txt").read_text(encoding="utf-8")
    assert "B_S6_L001_R1_001.fastq.gz" in (splicing_dir / "treatment_r1.txt").read_text(encoding="utf-8")


def test_regenerate_splicing_lists_reports_missing_manifest(tmp_path: Path):
    orchestrator = _orchestrator_stub()

    result = orchestrator._regenerate_splicing_lists(str(tmp_path))

    assert result["ok"] is False
    assert str(result["reason"]).startswith("missing_manifest:")
