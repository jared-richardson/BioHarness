from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bio_harness.core.fallback_skill_builder import (
    FallbackBuilderRequest,
    choose_repair_action,
    classify_failure_from_artifacts,
    read_run_artifacts,
    run_fallback_skill_builder,
    troubleshoot_runs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_fastq(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")


def _write_refs(workspace: Path) -> tuple[str, str]:
    inp = workspace / "inputs_readonly"
    inp.mkdir(parents=True, exist_ok=True)
    fasta = inp / "mouse_fasta"
    gtf = inp / "mouse_gtf"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    return str(fasta), str(gtf)


def _request(
    *,
    target_capability_set: list[str],
    allowed_tools: list[str],
    selected_dir: Path,
    data_root: Path,
    strictness_mode: str = "conservative",
    request_text: str = "",
    constraints: dict | None = None,
) -> FallbackBuilderRequest:
    return FallbackBuilderRequest.from_raw(
        target_capability_set=target_capability_set,
        allowed_tools=allowed_tools,
        data_reference_constraints=constraints or {},
        strictness_mode=strictness_mode,
        request_text=request_text,
        selected_dir=str(selected_dir),
        data_root=str(data_root),
    )


def test_selection_logic_prefers_reuse(tmp_path: Path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_refs(workspace)
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")

    req = _request(
        target_capability_set=["splicing_analysis", "alignment", "reference_inputs", "group_comparison"],
        allowed_tools=["star", "rmats", "samtools", "fastqc"],
        request_text="Run alternative splicing with rMATS for control vs treatment.",
        selected_dir=workspace,
        data_root=data_root,
    )

    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=req)
    assert report["decision"]["action"] == "reuse"
    assert report["contract_validation"]["passed"] is True


def test_request_from_raw_normalizes_unknown_strictness_mode() -> None:
    request = FallbackBuilderRequest.from_raw(
        target_capability_set=["alignment"],
        allowed_tools=["samtools"],
        data_reference_constraints={},
        strictness_mode="strict",
    )

    assert request.strictness_mode == "conservative"


def test_selection_logic_picks_extend_when_no_full_match(tmp_path: Path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_refs(workspace)
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")

    req = _request(
        target_capability_set=["alignment", "variant_calling", "annotation"],
        allowed_tools=["bcftools", "bwa", "samtools"],
        strictness_mode="aggressive",
        request_text="Call variants and add annotation in one fallback path.",
        selected_dir=workspace,
        data_root=data_root,
    )

    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=req)
    assert report["decision"]["action"] == "extend"
    assert "annotation" in report["decision"]["missing_capabilities"]


def test_selection_logic_composes_multi_domain_contract_when_enabled(tmp_path: Path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_refs(workspace)
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    (workspace / "query.faa").write_text(">p1\nMTEYKLVVVG\n", encoding="utf-8")

    req = _request(
        target_capability_set=["alignment", "variant_calling", "reference_inputs", "annotation", "protein_analysis"],
        allowed_tools=["bcftools", "bwa", "samtools", "blastp"],
        strictness_mode="aggressive",
        request_text="Run an all-in-one fallback covering variant calling and protein annotation.",
        selected_dir=workspace,
        data_root=data_root,
    )

    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=req)
    assert report["decision"]["action"] == "extend"
    assert report["composition"]["applied"] is True
    assert len(report["composition"]["selected_pipeline_ids"]) >= 2
    assert report["contract_validation"]["passed"] is True


def test_selection_logic_creates_when_catalog_has_no_overlap(tmp_path: Path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    workspace.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    req = _request(
        target_capability_set=["metabolomics_analysis"],
        allowed_tools=["metabo_tool"],
        request_text="Run metabolomics fallback.",
        selected_dir=workspace,
        data_root=data_root,
    )

    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=req)
    assert report["decision"]["action"] == "create"
    assert report["plan"]["canonical_template"].startswith("custom_")


def test_failure_classification_and_routing_from_artifacts(tmp_path: Path):
    run_dir = tmp_path / "runs" / "run_missing_ref"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps({"status": "failed", "error": ""}, indent=2), encoding="utf-8")
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    (run_dir / "execution.log").write_text("[Step 1 Output] [stdout] __MISSING_REFERENCE__:gtf\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("", encoding="utf-8")

    snapshot = read_run_artifacts(run_dir)
    classified = classify_failure_from_artifacts(snapshot)
    assert classified["failure_class"] == "missing_reference"
    assert choose_repair_action(classified["failure_class"], "conservative") == "repair_reference_bindings"


def test_troubleshoot_reruns_same_prompt_and_reports_regression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run_runtime_fail"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(
        json.dumps({"status": "failed", "error": "Step failed", "user_request": "echo repair"}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    (run_dir / "execution.log").write_text("exit code 1\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("runtime error\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "selected_dir": str(PROJECT_ROOT / "workspace"),
                "data_root": str(PROJECT_ROOT / "workspace" / "inputs_readonly"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def _fake_run(cmd, cwd=None, capture_output=True, text=True, check=False):
        result_idx = cmd.index("--result-json") + 1
        result_path = Path(cmd[result_idx])
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "run_id": "rerun_123",
                    "status": "completed",
                    "error": "",
                    "run_dir": str(PROJECT_ROOT / "workspace" / "runs" / "rerun_123"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    monkeypatch.setattr("bio_harness.core.fallback_skill_builder.subprocess.run", _fake_run)

    report = troubleshoot_runs(
        run_ids=[run_dir.name],
        workspace_root=workspace,
        project_root=PROJECT_ROOT,
        strictness_mode="aggressive",
        rerun_failures=True,
    )
    assert len(report["items"]) == 1
    item = report["items"][0]
    assert item["rerun"]["rerun_attempted"] is True
    assert item["regression_status"]["improved"] is True


@pytest.mark.parametrize("fixture_row", json.loads((Path(__file__).parent / "fixtures" / "fallback_skill_builder_requests.json").read_text(encoding="utf-8")))
def test_fixture_driven_plan_generation(tmp_path: Path, fixture_row: dict[str, Any]):
    workspace = tmp_path / fixture_row["name"] / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_refs(workspace)

    setup_kind = fixture_row.get("setup", "none")
    if setup_kind == "two_group_fastq":
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
        _write_fastq(data_root / "6_S6_R1_001.fastq")
        _write_fastq(data_root / "6_S6_R2_001.fastq")
    elif setup_kind == "single_pair":
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
    elif setup_kind == "counts":
        out = workspace / "outputs"
        out.mkdir(parents=True, exist_ok=True)
        (out / "counts.tsv").write_text(
            "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1\tS6\ng1\tchr1\t1\t2\t+\t2\t10\t11\n",
            encoding="utf-8",
        )
        (out / "metadata.tsv").write_text("sample\tcondition\nS1\tcontrol\nS6\ttreatment\n", encoding="utf-8")
    elif setup_kind == "protein":
        (workspace / "query.faa").write_text(">p1\nMTEYKLVVVG\n", encoding="utf-8")
    elif setup_kind == "single_pair_plus_protein":
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
        (workspace / "query.faa").write_text(">p1\nMTEYKLVVVG\n", encoding="utf-8")

    request = FallbackBuilderRequest.from_raw(
        target_capability_set=list(fixture_row.get("target_capability_set", [])),
        allowed_tools=list(fixture_row.get("allowed_tools", [])),
        data_reference_constraints={"subset_mode": True},
        strictness_mode=str(fixture_row.get("strictness_mode", "conservative")),
        request_text=str(fixture_row.get("request_text", "")),
        selected_dir=str(workspace),
        data_root=str(data_root),
    )
    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=request)

    assert report["decision"]["action"] in set(fixture_row.get("expected_actions", []))
    expected_token = str(fixture_row.get("expected_pipeline_token", "")).strip()
    if expected_token:
        blob = json.dumps(report.get("selection_details", {}), sort_keys=True).lower()
        assert expected_token.lower() in blob
    if "expect_composition" in fixture_row:
        assert bool(report.get("composition", {}).get("applied", False)) is bool(fixture_row.get("expect_composition"))
