"""Tests for fast-signal mini-benchmark preparation and validation."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from bio_harness.core.fast_signal_minibench import (
    DEFAULT_MINI_BENCHMARK_CONTRACTS,
    MINI_BENCHMARK_CASES,
    _synthetic_coding_reference,
    prepare_mini_benchmark_suite,
    selected_dir_for_mini_case,
    validate_mini_benchmark_contract,
)


def test_prepare_mini_benchmark_suite_writes_manifest_and_inputs(tmp_path: Path) -> None:
    payload = prepare_mini_benchmark_suite(tmp_path / "mini")
    manifest_path = Path(payload["manifest_file"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases_by_id = {case["id"]: case for case in manifest["cases"]}

    assert set(cases_by_id) == set(MINI_BENCHMARK_CASES)
    assert cases_by_id["de_mini"]["selected_dir"].endswith(
        "/official_runs/deseq/attempt1"
    )
    assert (
        tmp_path
        / "mini"
        / "tasks"
        / "evolution"
        / "data"
        / "evol1_R1.fastq.gz"
    ).is_file()
    with gzip.open(
        tmp_path / "mini" / "tasks" / "evolution" / "data" / "anc_R1.fastq.gz",
        "rt",
        encoding="utf-8",
    ) as handle:
        first_line = handle.readline().strip()
    assert first_line.startswith("@anc_")
    assert (
        tmp_path
        / "mini"
        / "tasks"
        / "germline-vc"
        / "data"
        / "ref_genome.fa"
    ).is_file()
    assert (
        tmp_path
        / "mini"
        / "tasks"
        / "deseq"
        / "references"
        / "C_parapsilosis_CDC317_current_features.gff"
    ).is_file()


def test_evolution_mini_reference_has_nonrepetitive_coding_signal() -> None:
    reference = _synthetic_coding_reference(2600)
    distinct_31mers = {
        reference[index : index + 31] for index in range(len(reference) - 30)
    }

    assert len(reference) == 2600
    assert reference[240:243] == "ATG"
    assert reference[2097:2100] == "TAA"
    assert len(distinct_31mers) > 2200


def test_selected_dir_for_mini_case_uses_strict_binder_layout(tmp_path: Path) -> None:
    selected_dir = selected_dir_for_mini_case(tmp_path / "mini", "de_mini")

    assert selected_dir == tmp_path / "mini" / "official_runs" / "deseq" / "attempt1"


def test_germline_contract_accepts_plain_or_bgzipped_vcf(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    final_dir = selected / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "variants.vcf").write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
        encoding="utf-8",
    )

    plain = validate_mini_benchmark_contract(
        selected,
        DEFAULT_MINI_BENCHMARK_CONTRACTS["germline_vc_mini"],
    )

    (final_dir / "variants.vcf").unlink()
    (final_dir / "variants.vcf.gz").write_bytes(b"bgzipped-placeholder\n")
    (final_dir / "variants.vcf.gz.tbi").write_bytes(b"index-placeholder\n")
    bgzipped = validate_mini_benchmark_contract(
        selected,
        DEFAULT_MINI_BENCHMARK_CONTRACTS["germline_vc_mini"],
    )

    assert plain["passed"] is True
    assert plain["matched_artifact"] == "final/variants.vcf"
    assert bgzipped["passed"] is True
    assert bgzipped["matched_artifact"] == "final/variants.vcf.gz"


def test_de_contract_accepts_canonical_and_legacy_result_names(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    final_dir = selected / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "deseq_results.csv").write_text(
        "gene_id,log2FoldChange,pvalue\n"
        "geneA,1.2,0.01\n",
        encoding="utf-8",
    )

    canonical = validate_mini_benchmark_contract(
        selected,
        DEFAULT_MINI_BENCHMARK_CONTRACTS["de_mini"],
    )

    (final_dir / "deseq_results.csv").unlink()
    (final_dir / "differential_expression.csv").write_text(
        "gene,log2FoldChange,pvalue\n"
        "geneA,1.2,0.01\n",
        encoding="utf-8",
    )
    legacy = validate_mini_benchmark_contract(
        selected,
        DEFAULT_MINI_BENCHMARK_CONTRACTS["de_mini"],
    )

    assert canonical["passed"] is True
    assert canonical["matched_artifact"] == "final/deseq_results.csv"
    assert legacy["passed"] is True
    assert legacy["matched_artifact"] == "final/differential_expression.csv"
