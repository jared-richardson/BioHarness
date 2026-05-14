from __future__ import annotations

from bio_harness.core.contract_inference_utils import requires_reference_inputs


def test_requires_reference_inputs_ignores_generic_references_word() -> None:
    text = (
        "Do not write anywhere outside the current run directory except reading "
        "the provided local benchmark inputs and references."
    )

    assert requires_reference_inputs(text) is False


def test_requires_reference_inputs_accepts_real_reference_assets() -> None:
    assert requires_reference_inputs("Use the provided reference genome for germline variant calling.") is True
    assert requires_reference_inputs("Perform transcript quantification using the transcriptome reference.") is True
    assert requires_reference_inputs("Use the local ClinVar reference at task_dir/references/clinvar.vcf.gz.") is True
