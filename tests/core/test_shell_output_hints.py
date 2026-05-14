from __future__ import annotations

from bio_harness.core.shell_output_hints import extract_shell_output_hints


def test_extract_shell_output_hints_captures_helper_script_outputs() -> None:
    hints = extract_shell_output_hints(
        "python3 pipeline_scripts/normalize_gff_for_featurecounts.py "
        "references/genes.gff references/annotation_for_featurecounts.gff"
    )

    assert hints.output_paths == ("references/annotation_for_featurecounts.gff",)
    assert hints.output_roots == ()


def test_extract_shell_output_hints_captures_redirections_and_output_roots() -> None:
    hints = extract_shell_output_hints(
        "bcftools view -Oz -o filtered/evol1_filtered.vcf.gz calls/evol1_raw.vcf && "
        "bcftools isec -w1 -p filtered/intersected filtered/evol1_filtered.vcf.gz filtered/anc_filtered.vcf.gz"
    )

    assert hints.output_paths == (
        "filtered/evol1_filtered.vcf.gz",
        "filtered/intersected/0000.vcf",
    )
    assert hints.output_roots == ()


def test_extract_shell_output_hints_supports_extra_flags() -> None:
    hints = extract_shell_output_hints(
        "python3 helper.py --report outputs/report.tsv --ref outputs/reference.tsv",
        extra_output_flags=("--report", "--ref"),
    )

    assert hints.output_paths == ("outputs/report.tsv", "outputs/reference.tsv")


def test_extract_shell_output_hints_ignores_comment_lines_and_keeps_output_roots() -> None:
    hints = extract_shell_output_hints(
        "cd selected && \\\n"
        "# build shared high/moderate variants\n"
        "bcftools isec -w1 -n=2 shared/evol1.vcf shared/evol2.vcf -p shared/isec && \\\n"
        "bcftools query -f '%CHROM\\n' shared/isec/0000.vcf > shared/out.csv"
    )

    assert hints.output_paths == ("shared/isec/0000.vcf", "shared/out.csv")
    assert hints.output_roots == ()


def test_extract_shell_output_hints_keeps_non_deterministic_isec_prefix_root() -> None:
    hints = extract_shell_output_hints(
        "bcftools isec -n=2 shared/evol1.vcf shared/evol2.vcf -p shared/isec"
    )

    assert hints.output_paths == ()
    assert hints.output_roots == ("shared/isec",)


def test_extract_shell_output_hints_captures_mv_and_inplace_bgzip_outputs() -> None:
    hints = extract_shell_output_hints(
        "mv isec_shared/0002.vcf shared/evol2_subtracted_anc.vcf && "
        "bgzip -f shared/evol2_subtracted_anc.vcf && "
        "tabix -f -p vcf shared/evol2_subtracted_anc.vcf.gz"
    )

    assert hints.output_paths == (
        "shared/evol2_subtracted_anc.vcf",
        "shared/evol2_subtracted_anc.vcf.gz",
        "shared/evol2_subtracted_anc.vcf.gz.tbi",
    )
    assert hints.output_roots == ()


def test_extract_shell_output_hints_discards_transient_isec_export_paths() -> None:
    hints = extract_shell_output_hints(
        "bcftools isec -w1 -n=2 -p .isec_export_evol1_anc_subtracted "
        "evol1_variants.vcf.gz anc_filtered.vcf.gz -Oz && "
        "mv -f .isec_export_evol1_anc_subtracted/0000.vcf.gz evol1_anc_subtracted.vcf.gz && "
        "rm -rf .isec_export_evol1_anc_subtracted && "
        "bcftools index evol1_anc_subtracted.vcf.gz"
    )

    assert hints.output_paths == ("evol1_anc_subtracted.vcf.gz",)
    assert hints.output_roots == ()


def test_extract_shell_output_hints_discards_removed_output_roots() -> None:
    hints = extract_shell_output_hints(
        "python3 helper.py --outdir tmp/work && rm -rf tmp/work && cat results.tsv > final/report.tsv"
    )

    assert hints.output_paths == ("final/report.tsv",)
    assert hints.output_roots == ()
