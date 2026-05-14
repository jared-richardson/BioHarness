from __future__ import annotations

from pathlib import Path

from bio_harness.core.de_wrapper_semantics import (
    validate_and_repair_de_wrapper_arguments,
)


def _write_metadata(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_validate_and_repair_de_wrapper_arguments_repairs_airway_style_prompt(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tdex\tcondition\n"
            "SRR1039508\tuntrt\tunknown\n"
            "SRR1039509\ttrt\tunknown\n"
            "SRR1039512\tuntrt\tunknown\n"
            "SRR1039513\ttrt\tunknown\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ treatment",
        "contrast": '["treatment", "dex", "untrt"]',
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert issues == []
    assert repaired["design_formula"] == "~ dex"
    assert repaired["contrast"] == "dex_trt_vs_untrt"
    assert "semantic_repaired:design_formula:~ treatment->~ dex" in fixes
    assert any(fix.startswith("semantic_repaired:contrast:") for fix in fixes)


def test_validate_and_repair_de_wrapper_arguments_appends_missing_factor_to_formula(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tbatch\tdex\n"
            "s1\tA\tuntrt\n"
            "s2\tA\ttrt\n"
            "s3\tB\tuntrt\n"
            "s4\tB\ttrt\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ batch",
        "contrast": "dex,trt,untrt",
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "edger_run",
        arguments,
    )

    assert issues == []
    assert repaired["design_formula"] == "~ batch + dex"
    assert repaired["contrast"] == "dex_trt_vs_untrt"
    assert "semantic_repaired:design_formula:~ batch->~ batch + dex" in fixes


def test_validate_and_repair_de_wrapper_arguments_expands_factor_only_contrast(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tdex\tcondition\n"
            "s1\tuntrt\tunknown\n"
            "s2\ttrt\tunknown\n"
            "s3\tuntrt\tunknown\n"
            "s4\ttrt\tunknown\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ dex",
        "contrast": "dex",
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert issues == []
    assert repaired["design_formula"] == "~ dex"
    assert repaired["contrast"] == "dex_trt_vs_untrt"
    assert any(fix.startswith("semantic_repaired:contrast:dex->") for fix in fixes)


def test_validate_and_repair_de_wrapper_arguments_maps_level_aliases(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tdex\tcondition\n"
            "s1\tuntrt\tunknown\n"
            "s2\ttrt\tunknown\n"
            "s3\tuntrt\tunknown\n"
            "s4\ttrt\tunknown\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ dex",
        "contrast": ["dex", "treated", "untreated"],
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert issues == []
    assert repaired["contrast"] == "dex_trt_vs_untrt"
    assert any("treated" in fix and "untrt" in fix for fix in fixes)


def test_validate_and_repair_de_wrapper_arguments_accepts_tab_delimited_csv_suffix(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "sample_metadata.csv",
        (
            "sample\tcondition\n"
            "SRR1278968\tcontrol\n"
            "SRR1278969\tcontrol\n"
            "SRR1278971\ttreatment\n"
            "SRR1278972\ttreatment\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ condition",
        "contrast": "condition_treatment_vs_control",
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert repaired == arguments
    assert issues == []
    assert fixes == []


def test_validate_and_repair_de_wrapper_arguments_maps_domain_aliases(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tcondition\n"
            "s1\tplanktonic\n"
            "s2\tbiofilm\n"
            "s3\tplanktonic\n"
            "s4\tbiofilm\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ condition",
        "contrast": ["condition", "biofilm", "planktonic"],
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert issues == []
    assert repaired["contrast"] == "condition_biofilm_vs_planktonic"
    assert any(fix.startswith("semantic_repaired:contrast:") for fix in fixes)


def test_validate_and_repair_de_wrapper_arguments_accepts_canonical_case_levels(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tcondition\n"
            "s1\tPlankton\n"
            "s2\tPlankton\n"
            "s3\tBiofilm\n"
            "s4\tBiofilm\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ condition",
        "contrast": "condition_Biofilm_vs_Plankton",
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert repaired == arguments
    assert issues == []
    assert fixes == []


def test_validate_and_repair_de_wrapper_arguments_uses_binary_semantic_fallback(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tcondition\n"
            "s1\tcontrol\n"
            "s2\ttreatment\n"
            "s3\tcontrol\n"
            "s4\ttreatment\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ condition",
        "contrast": ["condition", "affected_arm", "baseline_arm"],
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert issues == []
    assert repaired["contrast"] == "condition_treatment_vs_control"
    assert any("affected_arm" in fix and "treatment" in fix for fix in fixes)


def test_validate_and_repair_de_wrapper_arguments_blocks_ambiguous_invalid_bindings(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tdex\tbatch\n"
            "s1\tuntrt\tA\n"
            "s2\ttrt\tA\n"
            "s3\tuntrt\tB\n"
            "s4\ttrt\tB\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ treatment",
        "contrast": '["treatment", "dex", "untrt"]',
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "limma_voom_run",
        arguments,
    )

    assert repaired["design_formula"] == "~ treatment"
    assert repaired["contrast"] == '["treatment", "dex", "untrt"]'
    assert fixes == []
    assert "invalid_design_formula_columns:treatment" in issues
    assert "invalid_contrast_factor:treatment" in issues


def test_validate_and_repair_de_wrapper_arguments_drops_non_informative_design_factor(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tdex\tcondition\n"
            "s1\tuntrt\tunknown\n"
            "s2\ttrt\tunknown\n"
            "s3\tuntrt\tunknown\n"
            "s4\ttrt\tunknown\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ condition + dex",
        "contrast": "dex_trt_vs_untrt",
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert issues == []
    assert repaired["design_formula"] == "~ dex"
    assert "semantic_repaired:design_formula:~ condition + dex->~ dex" in fixes


def test_validate_and_repair_de_wrapper_arguments_repairs_single_level_contrast_factor(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        (
            "sample\tdex\tcondition\n"
            "s1\tuntrt\tunknown\n"
            "s2\ttrt\tunknown\n"
            "s3\tuntrt\tunknown\n"
            "s4\ttrt\tunknown\n"
        ),
    )
    arguments = {
        "counts_matrix": str(tmp_path / "counts.tsv"),
        "metadata_table": str(metadata_path),
        "design_formula": "~ condition + dex",
        "contrast": "condition_treatment_vs_control",
        "output_dir": str(tmp_path / "out"),
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "deseq2_run",
        arguments,
    )

    assert issues == []
    assert repaired["design_formula"] == "~ dex"
    assert repaired["contrast"] == "dex_trt_vs_untrt"
    assert any(fix.startswith("semantic_repaired:contrast:condition_treatment_vs_control->") for fix in fixes)


def test_validate_and_repair_de_wrapper_arguments_ignores_non_de_tools(tmp_path):
    metadata_path = _write_metadata(
        tmp_path / "metadata.tsv",
        "sample\tcondition\ns1\tcontrol\ns2\ttreat\n",
    )
    arguments = {
        "metadata_table": str(metadata_path),
        "design_formula": "~ treatment",
        "contrast": "condition,treat,control",
    }

    repaired, issues, fixes = validate_and_repair_de_wrapper_arguments(
        "scanpy_workflow",
        arguments,
    )

    assert repaired == arguments
    assert issues == []
    assert fixes == []
