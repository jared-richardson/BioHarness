from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.skills.library.bcftools_norm_run import bcftools_norm_run


def test_bcftools_norm_run_renders_atomic_helper_command() -> None:
    command = bcftools_norm_run(
        input_vcf="/tmp/input.vcf.gz",
        reference_fasta="/refs/genome.fa",
        output_vcf="/tmp/output.normalized.vcf.gz",
    )

    assert str(preferred_helper_python_executable()) in command
    assert "run_bcftools_norm.py" in command
    assert "--input-vcf" in command
    assert "--reference-fasta" in command
    assert "--output-vcf" in command
    assert "--multiallelic-mode" in command
    check = check_single_operation(command)
    assert check.passed is True


def test_bcftools_norm_run_supports_atomize_and_none_mode() -> None:
    command = bcftools_norm_run(
        input_vcf="/tmp/input.vcf",
        reference_fasta="/refs/genome.fa",
        output_vcf="/tmp/output.vcf",
        multiallelic_mode="none",
        atomize=True,
    )

    assert "--multiallelic-mode none" in command
    assert "--atomize" in command


def test_bcftools_norm_run_requires_core_paths() -> None:
    with pytest.raises(ValueError, match="input_vcf, reference_fasta, output_vcf"):
        bcftools_norm_run(input_vcf="/tmp/input.vcf.gz")


def test_run_bcftools_norm_splicer_rewrites_dash_any_value_fix_20() -> None:
    """Fix #20: argparse rejects ``--multiallelic-mode -any`` because ``-any``
    begins with ``-`` and looks like another option flag. The wrapper must
    splice the pair into ``--multiallelic-mode=-any`` so the value is consumed
    literally regardless of leading character.
    """

    from bio_harness.pipeline_scripts.run_bcftools_norm import _splice_multiallelic_mode_value

    spliced = _splice_multiallelic_mode_value(
        [
            "--input-vcf",
            "/tmp/in.vcf",
            "--reference-fasta",
            "/tmp/ref.fa",
            "--output-vcf",
            "/tmp/out.vcf",
            "--multiallelic-mode",
            "-any",
        ]
    )
    assert "--multiallelic-mode=-any" in spliced
    assert "-any" not in spliced  # the bare value was consumed, not left loose


def test_run_bcftools_norm_splicer_handles_plus_any_and_none_fix_20() -> None:
    """Fix #20: +any and none are also valid bcftools norm -m values. The
    splicer should canonicalize all three recognized forms and leave unknown
    values alone so argparse can still produce a meaningful error.
    """

    from bio_harness.pipeline_scripts.run_bcftools_norm import _splice_multiallelic_mode_value

    plus_any = _splice_multiallelic_mode_value(["--multiallelic-mode", "+any"])
    assert plus_any == ["--multiallelic-mode=+any"]

    none_val = _splice_multiallelic_mode_value(["--multiallelic-mode", "none"])
    assert none_val == ["--multiallelic-mode=none"]

    equals_form = _splice_multiallelic_mode_value(["--multiallelic-mode=-any"])
    assert equals_form == ["--multiallelic-mode=-any"]

    unknown = _splice_multiallelic_mode_value(["--multiallelic-mode", "-garbage"])
    assert unknown == ["--multiallelic-mode", "-garbage"]


def test_run_bcftools_norm_main_accepts_space_separated_mode_fix_20(tmp_path: Path) -> None:
    """Fix #20: end-to-end argparse invocation with the space-separated form
    no longer errors on ``expected one argument``. The wrapper still fails if
    bcftools itself is missing, but the CLI parsing step must succeed so the
    stepwise planner does not thrash on the same command shape.
    """

    from bio_harness.pipeline_scripts import run_bcftools_norm as module

    input_vcf = tmp_path / "in.vcf"
    input_vcf.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
        encoding="utf-8",
    )
    ref = tmp_path / "ref.fa"
    ref.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    output_vcf = tmp_path / "out.vcf"

    captured: dict[str, object] = {}

    def fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured["cmd"] = list(cmd)

        class R:
            returncode = 0

        return R()

    monkey = getattr(module.subprocess, "run")
    module.subprocess.run = fake_run  # type: ignore[assignment]
    try:
        rc = module.main(
            [
                "--input-vcf",
                str(input_vcf),
                "--reference-fasta",
                str(ref),
                "--output-vcf",
                str(output_vcf),
                "--multiallelic-mode",
                "-any",
            ]
        )
    finally:
        module.subprocess.run = monkey  # type: ignore[assignment]

    assert rc == 0
    cmd = captured["cmd"]
    assert "-m" in cmd
    assert cmd[cmd.index("-m") + 1] == "-any"
