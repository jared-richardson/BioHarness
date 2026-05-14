from __future__ import annotations

from pathlib import Path

from bio_harness.core.request_scope import (
    extract_request_paths,
    requested_skill_analysis_type,
    semantically_requests_long_read_rna_stringtie_pipeline,
)


def test_extract_request_paths_preserves_explicit_symlink_path(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    data_root = root / "extended_test_data" / "splicing_rmats"
    reference_root = root / "non_bioagent_real_data" / "ucsc"
    data_root.mkdir(parents=True)
    reference_root.mkdir(parents=True)
    target_gtf = reference_root / "hg19.chr14.knownGene.gtf"
    target_gtf.write_text("chr14\tsource\texon\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    annotation_gtf = data_root / "annotation.gtf"
    annotation_gtf.symlink_to(target_gtf)

    paths = extract_request_paths(
        f"Use the annotation GTF at {annotation_gtf}.",
        project_root=root,
    )

    assert paths == [annotation_gtf]


def test_semantically_requests_long_read_rna_stringtie_pipeline_detects_annotation_backed_raw_reads() -> None:
    assert semantically_requests_long_read_rna_stringtie_pipeline(
        (
            "These are Oxford Nanopore direct-RNA reads. Align them to the reference genome "
            "using the provided annotation and quantify transcript isoforms."
        )
    )


def test_semantically_requests_long_read_rna_stringtie_pipeline_rejects_annotation_free_case() -> None:
    assert not semantically_requests_long_read_rna_stringtie_pipeline(
        (
            "These are Oxford Nanopore direct-RNA reads. Align them to the reference genome "
            "and quantify transcript isoforms. No annotation file is provided."
        )
    )


def test_requested_skill_analysis_type_maps_gatk_to_germline_variant_calling() -> None:
    assert requested_skill_analysis_type("gatk_haplotypecaller") == "germline_variant_calling"
