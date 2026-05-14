from __future__ import annotations

from pathlib import Path

from bio_harness.harness.contract_utils import _repair_requested_references_and_index_bases_in_plan


def test_repair_requested_references_prefers_transcriptome_for_transcript_quant(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    transcriptome = data_root / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "salmon_quant",
                "step_id": 1,
                "arguments": {
                    "transcriptome_fasta": "/external/reference.fa",
                    "index_dir": "/external/salmon_index",
                    "reads_1": str(data_root / "reads_1.fastq.gz"),
                    "reads_2": str(data_root / "reads_2.fastq.gz"),
                    "output_dir": str(selected_dir / "salmon_quant"),
                },
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Quantify transcripts with Salmon using the staged transcriptome reference.",
    )

    args = repaired["plan"][0]["arguments"]
    assert args["transcriptome_fasta"] == str(transcriptome)
    assert args["index_dir"] == str(
        (selected_dir / "outputs" / "_cache" / "salmon_indexes" / transcriptome.name).resolve(strict=False)
    )
    assert meta["resolved_transcriptome_fasta"] == str(transcriptome)


def test_repair_requested_references_does_not_substitute_genome_for_missing_transcriptome(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "salmon_quant",
                "step_id": 1,
                "arguments": {
                    "reads_1": str(data_root / "reads_1.fastq.gz"),
                    "reads_2": str(data_root / "reads_2.fastq.gz"),
                    "output_dir": str(selected_dir / "salmon_quant"),
                },
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Quantify transcripts with Salmon from the available inputs.",
    )

    assert repaired == plan
    assert meta["changed"] is False
    assert meta["why"] == "no_reference_or_index_repairs"


def test_repair_requested_references_prefers_prebuilt_kallisto_index(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    transcriptome = data_root / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")
    kallisto_dir = data_root / "kallisto_index"
    kallisto_dir.mkdir(parents=True, exist_ok=True)
    prebuilt_index = kallisto_dir / "transcripts.idx"
    prebuilt_index.write_text("idx", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "kallisto_quant",
                "step_id": 1,
                "arguments": {
                    "transcriptome_fasta": "/external/transcripts.fa",
                    "index_path": "/external/transcripts.idx",
                    "reads_1": str(data_root / "reads_1.fastq.gz"),
                    "reads_2": str(data_root / "reads_2.fastq.gz"),
                    "output_dir": str(selected_dir / "kallisto_quant"),
                    "threads": 4,
                },
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Quantify transcripts with kallisto using the staged transcriptome reference.",
    )

    args = repaired["plan"][0]["arguments"]
    assert args["transcriptome_fasta"] == str(transcriptome)
    assert args["index_path"] == str(prebuilt_index)
    assert meta["resolved_transcriptome_fasta"] == str(transcriptome)


def test_repair_requested_references_preserves_explicit_external_gtf_for_stringtie(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    external_root = tmp_path / "external_refs"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    external_root.mkdir(parents=True, exist_ok=True)

    explicit_gtf = external_root / "chr14.gtf"
    explicit_gtf.write_text("chr14\tsource\texon\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    (data_root / "mouse_gtf").write_text("chr1\tsource\texon\t1\t10\t.\t+\t.\tgene_id \"mouse\";\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "step_id": 1,
                "arguments": {
                    "input_bam": str(tmp_path / "sample.bam"),
                    "annotation_gtf": str(explicit_gtf),
                    "output_gtf": str(selected_dir / "stringtie_output.gtf"),
                },
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        f"Run stringtie_quant on /tmp/sample.bam with annotation {explicit_gtf}.",
    )

    args = repaired["plan"][0]["arguments"]
    assert args["annotation_gtf"] == str(explicit_gtf)
    assert meta["resolved_gtf"] == str(explicit_gtf)
    assert meta["explicit_requested_references"]["gtf"] == str(explicit_gtf)
    assert meta["changed"] is False


def test_repair_requested_references_preserves_existing_data_root_gtf_without_explicit_request(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    alias_root = selected_dir / "inputs_readonly"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    alias_root.mkdir(parents=True, exist_ok=True)

    current_gtf = data_root / "custom_annotation.gtf"
    current_gtf.write_text("chr1\tsource\texon\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    (alias_root / "mouse_gtf").write_text("chr2\tsource\texon\t1\t10\t.\t+\t.\tgene_id \"mouse\";\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "step_id": 1,
                "arguments": {
                    "input_bam": str(tmp_path / "sample.bam"),
                    "annotation_gtf": str(current_gtf),
                    "output_gtf": str(selected_dir / "stringtie_output.gtf"),
                },
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Run stringtie_quant on the provided aligned BAM and annotation.",
    )

    args = repaired["plan"][0]["arguments"]
    assert args["annotation_gtf"] == str(current_gtf)
    assert meta["changed"] is False


def test_repair_requested_references_preserves_explicit_symlinked_gtf_path(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    reference_root = tmp_path / "reference_root"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    reference_root.mkdir(parents=True, exist_ok=True)

    target_gtf = reference_root / "hg19.chr14.knownGene.gtf"
    target_gtf.write_text("chr14\tsource\texon\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    explicit_gtf = data_root / "annotation.gtf"
    explicit_gtf.symlink_to(target_gtf)

    plan = {
        "plan": [
            {
                "tool_name": "rmats_run",
                "step_id": 1,
                "arguments": {
                    "group1_bams": "/tmp/treatment1.bam,/tmp/treatment2.bam",
                    "group2_bams": "/tmp/control1.bam,/tmp/control2.bam",
                    "annotation_gtf": str(explicit_gtf),
                    "output_dir": str(selected_dir / "splicing"),
                },
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        f"Use rmats_run with annotation GTF at {explicit_gtf}.",
    )

    args = repaired["plan"][0]["arguments"]
    assert args["annotation_gtf"] == str(explicit_gtf)
    assert meta["resolved_gtf"] == str(explicit_gtf)
    assert meta["explicit_requested_references"]["gtf"] == str(explicit_gtf)
    assert meta["changed"] is False


def test_repair_requested_references_uses_contextual_reference_fasta_when_prompt_has_two_fastas(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    human_ref = data_root / "MT-human.fa"
    orang_query = data_root / "MT-orang.fa"
    human_ref.write_text(">human\nACGT\n", encoding="utf-8")
    orang_query.write_text(">orang\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "minimap2_align",
                "step_id": 1,
                "arguments": {
                    "reference_fasta": str(human_ref),
                    "reads": str(orang_query),
                    "output_bam": str(selected_dir / "aligned" / "orang_vs_human.sam"),
                    "preset": "asm5",
                },
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        (
            f"Align the orangutan mitochondrial genome at {orang_query} to the human mitochondrial "
            f"reference at {human_ref} using minimap2_align with preset asm5."
        ),
    )

    args = repaired["plan"][0]["arguments"]
    assert args["reference_fasta"] == str(human_ref)
    assert args["reads"] == str(orang_query)
    assert meta["explicit_requested_references"]["fasta"] == str(human_ref)
    assert meta["changed"] is False
