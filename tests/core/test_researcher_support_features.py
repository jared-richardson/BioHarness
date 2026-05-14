from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bio_harness.core.reference_manager import (
    audit_reference_bundle,
    build_reference_materialization_plan,
    materialize_reference_bundle,
    write_reference_audit,
    write_reference_materialization_report,
)
from bio_harness.core.resource_preflight import assess_resource_preflight
from bio_harness.reporting.artifact_schema import profile_artifact_schema, write_artifact_schema_profile
from bio_harness.reporting.run_compare import compare_runs, write_run_comparison


def _make_fake_run(tmp_path: Path, *, label: str, status: str = "completed", repairs: int = 0) -> Path:
    selected_dir = tmp_path / label
    selected_dir.mkdir(parents=True)
    (selected_dir / "final").mkdir()
    (selected_dir / "final" / "results.csv").write_text("gene,log2fc\nA,1.2\n", encoding="utf-8")
    (selected_dir / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")
    (selected_dir / "harness.log").write_text("planning-heartbeat\n", encoding="utf-8")

    run_dir = tmp_path / f"{label}_run"
    run_dir.mkdir(parents=True)
    (run_dir / "planner").mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"PLANNER_ATTEMPT_STARTED","payload":{"attempt":1}}\n', encoding="utf-8")
    (run_dir / "execution.log").write_text("step complete\n", encoding="utf-8")
    (run_dir / "state.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    (run_dir / "planner" / "0001_hierarchical_plan_success.txt").write_text(
        json.dumps(
            {
                "thought_process": "fake plan",
                "plan": [
                    {"tool_name": "bash_run", "arguments": {"command": "echo hi > final.txt"}, "step_id": 1},
                    {"tool_name": "artifact_schema_profile", "arguments": {"input_path": "/tmp/results.csv"}, "step_id": 2},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (selected_dir / "result.json").write_text(
        json.dumps(
            {
                "selected_dir": str(selected_dir),
                "run_dir": str(run_dir),
                "status": status,
                "benchmark_policy": "scientific_harness",
                "auto_repair_history_count": repairs,
                "planning_attempts": [{"attempt": 1}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return selected_dir


def test_artifact_schema_profiles_csv_and_vcf(tmp_path: Path) -> None:
    csv_path = tmp_path / "results.csv"
    csv_path.write_text("gene,log2fc,padj\nA,1.2,0.01\nB,-0.5,0.2\n", encoding="utf-8")
    vcf_path = tmp_path / "variants.vcf"
    vcf_path.write_text(
        "##fileformat=VCFv4.2\n"
        "##INFO=<ID=ANN,Number=.,Type=String,Description=\"Annotation\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
        "chr1\t10\t.\tA\tG\t50\tPASS\tANN=G|missense_variant\tGT\t0/1\n",
        encoding="utf-8",
    )

    csv_profile = profile_artifact_schema(csv_path)
    vcf_profile = profile_artifact_schema(vcf_path)
    output_json = write_artifact_schema_profile(csv_path)

    assert csv_profile["format"] == "csv"
    assert [column["name"] for column in csv_profile["columns"]] == ["gene", "log2fc", "padj"]
    assert vcf_profile["format"] == "vcf"
    assert "ANN" in vcf_profile["info_fields"]
    assert output_json.exists()


def test_compare_runs_writes_json_and_markdown(tmp_path: Path) -> None:
    run_a = _make_fake_run(tmp_path, label="run_a", repairs=0)
    run_b = _make_fake_run(tmp_path, label="run_b", repairs=1)
    (run_b / "final" / "extra.tsv").write_text("x\ty\n", encoding="utf-8")

    summary = compare_runs(run_a, run_b)
    output_dir = write_run_comparison(run_a, run_b)

    assert summary["diff"]["auto_repair_delta"] == 1
    assert "final/extra.tsv" in summary["diff"]["only_in_run_b_outputs"]
    assert (output_dir / "comparison.json").exists()
    assert (output_dir / "comparison.md").exists()
    assert (output_dir / "quality_comparison.json").exists()
    assert (output_dir / "quality_comparison.md").exists()
    quality = json.loads((output_dir / "quality_comparison.json").read_text(encoding="utf-8"))
    assert "overall_winner" in quality


def test_assess_resource_preflight_reports_resource_shortfalls(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("bio_harness.core.resource_preflight.psutil.virtual_memory", lambda: SimpleNamespace(available=2 * 1024**3))
    monkeypatch.setattr("bio_harness.core.resource_preflight.psutil.cpu_count", lambda logical=True: 2)
    monkeypatch.setattr("bio_harness.core.resource_preflight.shutil.disk_usage", lambda path: SimpleNamespace(total=100, used=90, free=5 * 1024**3))

    payload = assess_resource_preflight(
        ["spades_assemble", "artifact_schema_profile"],
        selected_dir=tmp_path,
        min_free_disk_gb=10.0,
    )

    assert payload["ok"] is False
    assert payload["requirements"]["min_ram_gb"] >= 2.0
    assert payload["requirements"]["estimated_free_disk_gb"] >= 10.0
    assert any("available memory" in warning for warning in payload["warnings"])
    assert any("free disk" in warning for warning in payload["warnings"])


def test_assess_resource_preflight_estimates_heavy_temp_and_index_disk(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("bio_harness.core.resource_preflight.psutil.virtual_memory", lambda: SimpleNamespace(available=64 * 1024**3))
    monkeypatch.setattr("bio_harness.core.resource_preflight.psutil.cpu_count", lambda logical=True: 16)
    monkeypatch.setattr("bio_harness.core.resource_preflight.shutil.disk_usage", lambda path: SimpleNamespace(total=100, used=60, free=30 * 1024**3))

    payload = assess_resource_preflight(
        ["spades_assemble", "salmon_quant"],
        selected_dir=tmp_path,
        min_free_disk_gb=10.0,
    )

    assert payload["ok"] is False
    assert payload["disk_estimate"]["estimated_temp_disk_gb"] == 40.0
    assert payload["disk_estimate"]["estimated_reference_build_disk_gb"] == 6.0
    assert payload["requirements"]["estimated_free_disk_gb"] == 46.0
    assert payload["disk_estimate"]["drivers"] == [
        {"skill_name": "spades_assemble", "estimated_temp_disk_gb": 40.0},
        {"skill_name": "salmon_quant", "estimated_reference_build_disk_gb": 6.0},
    ]
    assert any("estimated workflow requirement 46.00 GiB" in warning for warning in payload["warnings"])


def test_audit_reference_bundle_detects_common_indexes(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    (refs / "genome.fa.fai").write_text("chr1\t4\t0\t4\t5\n", encoding="utf-8")
    (refs / "genome.dict").write_text("@HD\tVN:1.6\n", encoding="utf-8")
    (refs / "genes.gtf").write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    (refs / "genome.amb").write_text("", encoding="utf-8")
    (refs / "genome.ann").write_text("", encoding="utf-8")
    (refs / "genome.bwt").write_text("", encoding="utf-8")
    (refs / "genome.pac").write_text("", encoding="utf-8")
    (refs / "genome.sa").write_text("", encoding="utf-8")
    bowtie_dir = refs / "bowtie"
    bowtie_dir.mkdir()
    (bowtie_dir / "genome.1.bt2").write_text("", encoding="utf-8")
    salmon_dir = refs / "salmon_index"
    salmon_dir.mkdir()
    (salmon_dir / "hash.bin").write_text("", encoding="utf-8")
    kallisto_dir = refs / "kallisto_index"
    kallisto_dir.mkdir()
    (kallisto_dir / "transcripts.idx").write_text("", encoding="utf-8")
    star_dir = refs / "star_index"
    star_dir.mkdir()
    (star_dir / "genomeParameters.txt").write_text("genomeFastaFiles genome.fa\n", encoding="utf-8")
    (star_dir / "SA").write_text("", encoding="utf-8")
    (refs / "genome.mmi").write_text("", encoding="utf-8")

    summary = audit_reference_bundle(refs)

    assert "genome.fa" in summary["fasta_files"]
    assert "genes.gtf" in summary["annotation_files"]
    assert summary["primary_fasta"] == "genome.fa"
    assert summary["primary_transcriptome_fasta"] is None
    assert summary["primary_annotation"] == "genes.gtf"
    assert summary["selection_issues"] == []
    assert summary["transcriptome_fasta_files"] == []
    assert any(prefix.endswith("genome") for prefix in summary["bwa_index_prefixes"])
    assert any(prefix.endswith("bowtie/genome") for prefix in summary["bowtie2_index_prefixes"])
    assert "salmon_index" in summary["salmon_index_dirs"]
    assert "kallisto_index/transcripts.idx" in summary["kallisto_index_files"]
    assert "star_index" in summary["star_index_dirs"]
    assert "genome.mmi" in summary["minimap2_indices"]


def test_build_reference_materialization_plan_marks_missing_safe_targets(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda name: name in {"samtools", "bwa"})
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs)

    statuses = {row["target"]: row["status"] for row in plan["steps"]}
    assert statuses["faidx"] == "pending"
    assert statuses["bwa"] == "pending"
    assert statuses["dict"] == "missing_tool"
    assert statuses["bowtie2"] == "missing_tool"
    assert "faidx" in plan["pending_targets"]
    assert "dict" in plan["unavailable_targets"]


def test_build_reference_materialization_plan_resolves_bwa_mem2_alias(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda name: name == "bwa")
    monkeypatch.setattr(
        "bio_harness.core.reference_manager.which_with_pixi",
        lambda name: "/usr/bin/bwa-mem2" if name == "bwa" else None,
    )

    plan = build_reference_materialization_plan(refs, targets=["bwa"])

    assert plan["steps"] == [
        {
            "target": "bwa",
            "status": "pending",
            "tool": "bwa",
            "command": ["/usr/bin/bwa-mem2", "index", str(fasta)],
            "outputs": [
                "genome.amb",
                "genome.ann",
                "genome.pac",
                "genome.sa",
            ],
        }
    ]
    assert plan["pending_targets"] == ["bwa"]
    assert plan["unavailable_targets"] == []
    assert plan["ready"] is False


def test_materialize_reference_bundle_runs_pending_steps(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda name: name == "samtools")
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    commands: list[list[str]] = []

    def _fake_run(argv, cwd, capture_output, text, check):
        commands.append(list(argv))
        (refs / "genome.fa.fai").write_text("chr1\t4\t0\t4\t5\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("bio_harness.core.reference_manager.subprocess.run", _fake_run)

    report = materialize_reference_bundle(refs, targets=["faidx"])

    assert report["success"] is True
    assert commands == [["/usr/bin/samtools", "faidx", str(fasta)]]
    assert report["steps"][0]["returncode"] == 0


def test_build_reference_materialization_plan_skips_prebuilt_indexes(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    (refs / "genome.fa.fai").write_text("chr1\t4\t0\t4\t5\n", encoding="utf-8")
    (refs / "genome.dict").write_text("@HD\tVN:1.6\n", encoding="utf-8")
    (refs / "genome.mmi").write_text("", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda _name: True)
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, targets=["faidx", "dict", "minimap2"])

    statuses = {row["target"]: row["status"] for row in plan["steps"]}
    assert statuses == {
        "faidx": "present",
        "dict": "present",
        "minimap2": "present",
    }
    assert plan["pending_targets"] == []
    assert plan["ready"] is True


def test_build_reference_materialization_plan_supports_extended_targets(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    transcriptome = refs / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")
    annotation = refs / "genes.gtf"
    annotation.write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda _name: True)
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, include_extended=True)

    steps = {row["target"]: row for row in plan["steps"]}
    assert steps["star"]["status"] == "pending"
    assert steps["star"]["command"] == [
        "/usr/bin/star",
        "--runMode",
        "genomeGenerate",
        "--runThreadN",
        "2",
        "--genomeDir",
        str(refs / "star_index"),
        "--genomeFastaFiles",
        str(fasta),
        "--sjdbGTFfile",
        str(annotation),
    ]
    assert steps["salmon"]["status"] == "pending"
    assert steps["salmon"]["command"] == [
        "/usr/bin/salmon",
        "index",
        "-t",
        str(transcriptome),
        "-i",
        str(refs / "salmon_index"),
    ]
    assert steps["kallisto"]["status"] == "pending"
    assert steps["kallisto"]["command"] == [
        "/usr/bin/kallisto",
        "index",
        "-i",
        str(refs / "kallisto_index" / "transcripts.idx"),
        str(transcriptome),
    ]
    assert set(plan["pending_targets"]) >= {"star", "salmon", "kallisto"}
    assert plan["primary_transcriptome_fasta"] == str(transcriptome)


def test_build_reference_materialization_plan_marks_salmon_missing_transcriptome(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda _name: True)
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, targets=["salmon"])

    assert plan["primary_fasta"] == str(fasta)
    assert plan["primary_transcriptome_fasta"] is None
    assert plan["steps"] == [
        {
            "target": "salmon",
            "status": "missing_transcriptome_fasta",
            "required_asset": "primary_transcriptome_fasta",
            "outputs": ["salmon_index/hash.bin"],
        }
    ]
    assert plan["pending_targets"] == []
    assert plan["unavailable_targets"] == ["salmon"]
    assert plan["ready"] is False


def test_build_reference_materialization_plan_marks_kallisto_missing_transcriptome(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda _name: True)
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, targets=["kallisto"])

    assert plan["primary_fasta"] == str(fasta)
    assert plan["primary_transcriptome_fasta"] is None
    assert plan["steps"] == [
        {
            "target": "kallisto",
            "status": "missing_transcriptome_fasta",
            "required_asset": "primary_transcriptome_fasta",
            "outputs": ["kallisto_index/transcripts.idx"],
        }
    ]
    assert plan["pending_targets"] == []
    assert plan["unavailable_targets"] == ["kallisto"]
    assert plan["ready"] is False


def test_build_reference_materialization_plan_supports_salmon_from_transcriptome_only_bundle(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    transcriptome = refs / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda _name: True)
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, targets=["salmon"])

    assert plan["primary_fasta"] is None
    assert plan["primary_transcriptome_fasta"] == str(transcriptome)
    assert plan["steps"] == [
        {
            "target": "salmon",
            "status": "pending",
            "tool": "salmon",
            "command": [
                "/usr/bin/salmon",
                "index",
                "-t",
                str(transcriptome),
                "-i",
                str(refs / "salmon_index"),
            ],
            "outputs": ["salmon_index/hash.bin"],
        }
    ]
    assert plan["pending_targets"] == ["salmon"]


def test_build_reference_materialization_plan_skips_prebuilt_kallisto_index(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    transcriptome = refs / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")
    kallisto_dir = refs / "kallisto_index"
    kallisto_dir.mkdir()
    (kallisto_dir / "transcripts.idx").write_text("", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda _name: True)
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, targets=["kallisto"])

    assert plan["steps"] == [
        {
            "target": "kallisto",
            "status": "present",
            "outputs": ["kallisto_index/transcripts.idx"],
        }
    ]
    assert plan["pending_targets"] == []
    assert plan["ready"] is True


def test_build_reference_materialization_plan_marks_star_missing_annotation(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda _name: True)
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, targets=["star"])

    assert plan["steps"] == [
        {
            "target": "star",
            "status": "missing_annotation",
            "outputs": ["star_index/genomeParameters.txt"],
        }
    ]
    assert plan["pending_targets"] == []
    assert plan["unavailable_targets"] == ["star"]
    assert plan["ready"] is False


def test_materialize_reference_bundle_dry_run_marks_pending_steps_without_execution(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda name: name == "samtools")
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    report = materialize_reference_bundle(refs, targets=["faidx"], dry_run=True)

    assert report["success"] is True
    assert report["steps"] == [
        {
            "target": "faidx",
            "status": "pending",
            "tool": "samtools",
            "command": ["/usr/bin/samtools", "faidx", str(fasta)],
            "outputs": ["genome.fa.fai"],
            "returncode": 0,
            "dry_run": True,
        }
    ]
    assert not (refs / "genome.fa.fai").exists()


def test_materialize_reference_bundle_stops_on_first_failure(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda name: name in {"samtools", "bwa"})
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    commands: list[list[str]] = []

    def _fake_run(argv, cwd, capture_output, text, check):
        commands.append(list(argv))
        if argv[1] == "faidx":
            return SimpleNamespace(returncode=1, stdout="", stderr="failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("bio_harness.core.reference_manager.subprocess.run", _fake_run)

    report = materialize_reference_bundle(refs, targets=["faidx", "bwa"])

    assert report["success"] is False
    assert commands == [["/usr/bin/samtools", "faidx", str(fasta)]]
    assert report["steps"][0]["returncode"] == 1


def test_write_reference_reports_emit_json(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    fasta = refs / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    (refs / "genome.fa.fai").write_text("chr1\t4\t0\t4\t5\n", encoding="utf-8")

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda name: name == "samtools")
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    audit_path = write_reference_audit(refs)
    materialization_path = write_reference_materialization_report(refs, targets=["faidx"], dry_run=True)

    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    materialization_payload = json.loads(materialization_path.read_text(encoding="utf-8"))

    assert audit_payload["reference_root"] == str(refs.resolve())
    assert materialization_payload["reference_root"] == str(refs.resolve())
    assert materialization_payload["steps"] == [
        {
            "target": "faidx",
            "status": "present",
            "outputs": ["genome.fa.fai"],
        }
    ]


def test_audit_reference_bundle_detects_gzipped_fasta(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "genome.fa.gz").write_text("stub", encoding="utf-8")

    summary = audit_reference_bundle(refs)

    assert "genome.fa.gz" in summary["fasta_files"]
    assert summary["primary_fasta"] == "genome.fa.gz"


def test_build_reference_materialization_plan_fails_fast_on_multiple_fastas_without_manifest(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "a.fa").write_text(">a\nACGT\n", encoding="utf-8")
    (refs / "b.fa").write_text(">b\nACGT\n", encoding="utf-8")

    plan = build_reference_materialization_plan(refs)

    assert plan["ready"] is False
    assert plan["reason"] == "ambiguous_primary_primary_fasta"
    assert plan["steps"] == []
    assert any(issue["code"] == "ambiguous_primary_primary_fasta" for issue in plan["selection_issues"])


def test_build_reference_materialization_plan_fails_fast_on_multiple_annotations_without_manifest(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (refs / "a.gtf").write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    (refs / "b.gtf").write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tgene_id \"g2\";\n", encoding="utf-8")

    plan = build_reference_materialization_plan(refs)

    assert plan["ready"] is False
    assert plan["reason"] == "ambiguous_primary_primary_annotation"
    assert plan["steps"] == []
    assert any(issue["code"] == "ambiguous_primary_primary_annotation" for issue in plan["selection_issues"])


def test_build_reference_materialization_plan_uses_manifest_selected_primary_assets(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "a.fa").write_text(">a\nACGT\n", encoding="utf-8")
    fasta = refs / "b.fa"
    fasta.write_text(">b\nACGT\n", encoding="utf-8")
    (refs / "a.gtf").write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    annotation = refs / "b.gtf"
    annotation.write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tgene_id \"g2\";\n", encoding="utf-8")
    (refs / "reference_manifest.json").write_text(
        json.dumps({"primary_fasta": "b.fa", "primary_annotation": "b.gtf"}, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr("bio_harness.core.reference_manager.requirement_available", lambda name: name == "samtools")
    monkeypatch.setattr("bio_harness.core.reference_manager.which_with_pixi", lambda name: f"/usr/bin/{name}")

    plan = build_reference_materialization_plan(refs, targets=["faidx"])

    assert plan["primary_fasta"] == str(fasta)
    assert plan["primary_annotation"] == str(annotation)
    assert plan["steps"] == [
        {
            "target": "faidx",
            "status": "pending",
            "tool": "samtools",
            "command": ["/usr/bin/samtools", "faidx", str(fasta)],
            "outputs": ["b.fa.fai"],
        }
    ]


def test_build_reference_materialization_plan_detects_manifest_index_mismatch(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "a.fa").write_text(">a\nACGT\n", encoding="utf-8")
    fasta = refs / "b.fa"
    fasta.write_text(">b\nACGT\n", encoding="utf-8")
    (refs / "a.fa.fai").write_text("a\t4\t0\t4\t5\n", encoding="utf-8")
    (refs / "reference_manifest.json").write_text(
        json.dumps({"primary_fasta": "b.fa"}, indent=2),
        encoding="utf-8",
    )

    plan = build_reference_materialization_plan(refs, targets=["faidx"])

    assert plan["ready"] is False
    assert plan["reason"] == "manifest_index_mismatch_faidx"
    assert plan["steps"] == []
    assert any(issue["code"] == "manifest_index_mismatch_faidx" for issue in plan["selection_issues"])
