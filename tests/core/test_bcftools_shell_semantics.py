"""Tests for bcftools shell semantic inspection and repair."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.bcftools_shell_semantics import (
    inspect_bcftools_expression_command,
    inspect_bcftools_isec_command,
    repair_bcftools_expression_command,
    repair_bcftools_isec_command,
)
from bio_harness.harness.plan_semantic_guards import (
    repair_invalid_bcftools_isec_bash_run_commands,
)


def _write_ambiguous_single_sample_vcf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##INFO=<ID=AF,Number=1,Type=Float,Description=\"Allele frequency\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=9;AF=0.9\tDP\t9\n"
        ),
        encoding="utf-8",
    )


def test_inspect_and_repair_bcftools_expression_command_handles_continuation_prefix(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    input_vcf = selected_dir / "calls" / "evol1_raw.vcf"
    _write_ambiguous_single_sample_vcf(input_vcf)
    command = (
        f"cd {selected_dir} && \\\n"
        "bcftools view -i 'QUAL>=30 && DP>=5 && AF>=0.8' "
        "-Oz -o filtered/evol1_filtered.vcf.gz calls/evol1_raw.vcf"
    )

    issues = inspect_bcftools_expression_command(command, cwd=selected_dir)
    repaired_command, repairs = repair_bcftools_expression_command(command, cwd=selected_dir)

    assert [issue["tag"] for issue in issues] == ["DP"]
    assert repairs and repairs[0]["preferred_namespace"] == "INFO"
    assert "INFO/DP>=5" in repaired_command
    assert "\\\nbcftools" not in repaired_command


def test_repair_bcftools_expression_command_rewrites_missing_format_af_to_info_af(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    input_vcf = selected_dir / "calls" / "evol1_raw.vcf"
    _write_ambiguous_single_sample_vcf(input_vcf)
    command = (
        f"bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/AF>=0.8' "
        f"-Oz -o {selected_dir / 'filtered.vcf.gz'} {input_vcf}"
    )

    issues = inspect_bcftools_expression_command(command, cwd=selected_dir)
    repaired_command, repairs = repair_bcftools_expression_command(command, cwd=selected_dir)

    assert any(issue["issue"] == "missing_bcftools_expression_namespace_field" for issue in issues)
    assert any(repair["issue"] == "missing_bcftools_expression_namespace_field" for repair in repairs)
    assert "FORMAT/AF>=0.8" not in repaired_command
    assert "INFO/AF>=0.8" in repaired_command
    assert "FORMAT/DP>=5" in repaired_command


def test_repair_bcftools_isec_command_rewrites_prefix_output_target_export() -> None:
    command = (
        "bcftools isec -C -w1 -p . "
        "/tmp/a.vcf.gz /tmp/b.vcf.gz -Oz -o evol1_no_anc.vcf.gz"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "prefix_output_target_ignored" for issue in issues)
    assert repairs
    assert "-o evol1_no_anc.vcf.gz" not in repaired_command
    assert "mv -f .isec_export_evol1_no_anc/0000.vcf.gz evol1_no_anc.vcf.gz" in repaired_command


def test_repair_bcftools_isec_command_rewrites_direct_output_target_without_prefix() -> None:
    command = (
        "bcftools isec -C -w1 "
        "/tmp/a.vcf.gz /tmp/b.vcf.gz -Oz -o evol1_no_anc.vcf.gz"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "prefix_output_target_ignored" for issue in issues)
    assert repairs
    assert " -Oz -o evol1_no_anc.vcf.gz" not in repaired_command
    assert "bcftools isec -C -w1 /tmp/a.vcf.gz /tmp/b.vcf.gz -Oz -p .isec_export_evol1_no_anc" in repaired_command
    assert "mv -f .isec_export_evol1_no_anc/0000.vcf.gz evol1_no_anc.vcf.gz" in repaired_command


def test_repair_bcftools_isec_command_rewrites_prefix_pipeline_export() -> None:
    command = (
        "bcftools isec -w1 /tmp/a.vcf /tmp/b.vcf -p isec_tmp "
        "| bgzip -c > shared_annotated.vcf.gz"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "prefix_pipeline_stdout_ignored" for issue in issues)
    assert repairs
    assert " -p isec_tmp | bgzip -c > shared_annotated.vcf.gz" not in repaired_command
    assert "bgzip -c /tmp/a.vcf > /tmp/a.vcf.gz" in repaired_command
    assert "tabix -f /tmp/a.vcf.gz" in repaired_command
    assert "bgzip -c isec_tmp/0000.vcf > shared_annotated.vcf.gz" in repaired_command


def test_repair_bcftools_isec_command_rewrites_bgzip_pipeline_export_without_dash_c() -> None:
    command = (
        "bcftools isec -w1 /tmp/a.vcf /tmp/b.vcf -p . "
        "| bgzip > shared_annotated.vcf.gz"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "prefix_pipeline_stdout_ignored" for issue in issues)
    assert repairs
    assert " | bgzip > shared_annotated.vcf.gz" not in repaired_command
    assert "bgzip -c /tmp/a.vcf > /tmp/a.vcf.gz" in repaired_command
    assert "tabix -f /tmp/a.vcf.gz" in repaired_command
    assert "bgzip -c .isec_export_shared_annotated/0000.vcf > shared_annotated.vcf.gz" in repaired_command


def test_repair_invalid_bcftools_isec_bash_run_commands_updates_plan() -> None:
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools isec -C -w1 -p . "
                        "/tmp/a.vcf.gz /tmp/b.vcf.gz -Oz -o evol1_no_anc.vcf.gz"
                    )
                },
            }
        ]
    }

    repaired, meta = repair_invalid_bcftools_isec_bash_run_commands(plan)

    assert meta["changed"] is True
    assert "mv -f .isec_export_evol1_no_anc/0000.vcf.gz evol1_no_anc.vcf.gz" in repaired["plan"][0]["arguments"]["command"]


def test_repair_bcftools_isec_command_rewrites_overbroad_prefix_root_followup_consumer() -> None:
    command = (
        "mkdir -p /tmp/selected && cd /tmp/selected && "
        "bcftools isec -c none -p . evol1_norm.vcf.gz evol2_norm.vcf.gz && "
        "awk 'BEGIN{print \"CHROM\"}' 0000.vcf | "
        "sed '1i header' > shared_variants_moderate_high.csv"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "overbroad_prefix_root" for issue in issues)
    assert any(repair["reason"] == "overbroad_prefix_root" for repair in repairs)
    assert "bcftools isec -c none -p . " not in repaired_command
    assert "bcftools isec -c none -p .isec_export_shared_variants_moderate_high" in repaired_command
    assert " 0000.vcf " not in repaired_command
    assert ".isec_export_shared_variants_moderate_high/0000.vcf" in repaired_command
    assert "rm -rf .isec_export_shared_variants_moderate_high" in repaired_command


def test_repair_invalid_bcftools_isec_bash_run_commands_handles_fix25_bgzip_pipeline_shape() -> None:
    plan = {
        "plan": [
            {
                "step_id": 10,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "mkdir -p /tmp/selected && "
                        "bcftools isec -w1 -n=2 -p . "
                        "ancestor_filtered.vcf.gz evol1_call/evol1_raw.vcf "
                        "| bgzip > evol1_ancestor_subtracted.vcf.gz && "
                        "bcftools index evol1_ancestor_subtracted.vcf.gz"
                    )
                },
            }
        ]
    }

    repaired, meta = repair_invalid_bcftools_isec_bash_run_commands(plan)

    assert meta["changed"] is True
    repaired_command = repaired["plan"][0]["arguments"]["command"]
    assert "bgzip -c evol1_call/evol1_raw.vcf > evol1_call/evol1_raw.vcf.gz" in repaired_command
    assert "tabix -f evol1_call/evol1_raw.vcf.gz" in repaired_command
    assert "bcftools isec -w1 -n=2 -p .isec_export_evol1_ancestor_subtracted" in repaired_command
    assert "bgzip -c .isec_export_evol1_ancestor_subtracted/0000.vcf > evol1_ancestor_subtracted.vcf.gz" in repaired_command


def test_repair_bcftools_isec_command_stages_plain_vcf_inputs_without_other_export_changes() -> None:
    command = (
        "bcftools isec -p shared_intersect evol1_subtracted.vcf evol2_subtracted.vcf && "
        "cat shared_intersect/0000.vcf shared_intersect/0001.vcf > shared_raw.vcf"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "plain_vcf_input_requires_bgzip" for issue in issues)
    assert repairs
    assert "bgzip -c evol1_subtracted.vcf > evol1_subtracted.vcf.gz" in repaired_command
    assert "bgzip -c evol2_subtracted.vcf > evol2_subtracted.vcf.gz" in repaired_command
    assert "tabix -f evol1_subtracted.vcf.gz" in repaired_command
    assert "tabix -f evol2_subtracted.vcf.gz" in repaired_command
    assert "bcftools isec -p shared_intersect evol1_subtracted.vcf.gz evol2_subtracted.vcf.gz" in repaired_command


def test_repair_invalid_bcftools_isec_bash_run_commands_handles_overbroad_shared_export_prefix() -> None:
    plan = {
        "plan": [
            {
                "step_id": 14,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "mkdir -p /tmp/selected && cd /tmp/selected && "
                        "bcftools isec -c none -p . evol1_norm.vcf.gz evol2_norm.vcf.gz && "
                        "awk 'BEGIN{print \"CHROM\"}' 0000.vcf | "
                        "sed '1i header' > shared_variants_moderate_high.csv"
                    )
                },
            }
        ]
    }

    repaired, meta = repair_invalid_bcftools_isec_bash_run_commands(plan)

    assert meta["changed"] is True
    repaired_command = repaired["plan"][0]["arguments"]["command"]
    assert "bcftools isec -c none -p .isec_export_shared_variants_moderate_high" in repaired_command
    assert ".isec_export_shared_variants_moderate_high/0000.vcf" in repaired_command
    assert "rm -rf .isec_export_shared_variants_moderate_high" in repaired_command


def test_repair_bcftools_isec_command_handles_overbroad_prefix_root_move_export() -> None:
    command = (
        "bcftools isec -C -w1 ancestor_filtered.vcf.gz evol1_filtered.vcf.gz -p . && "
        "mv evol1_filtered.vcf.gz evol1_subtracted_anc.vcf.gz && "
        "mv evol1_filtered.vcf.gz.tbi evol1_subtracted_anc.vcf.gz.tbi"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "overbroad_prefix_root" for issue in issues)
    assert any(repair["reason"] == "overbroad_prefix_root" for repair in repairs)
    assert "bcftools isec -C -w1 ancestor_filtered.vcf.gz evol1_filtered.vcf.gz -p . &&" not in repaired_command
    assert "bcftools isec -C -w1 ancestor_filtered.vcf.gz evol1_filtered.vcf.gz -p .isec_export_evol1_subtracted_anc" in repaired_command
    assert "bgzip -c .isec_export_evol1_subtracted_anc/0000.vcf > evol1_subtracted_anc.vcf.gz" in repaired_command
    assert "tabix -f -p vcf evol1_subtracted_anc.vcf.gz" in repaired_command
    assert "mv evol1_filtered.vcf.gz evol1_subtracted_anc.vcf.gz" not in repaired_command
    assert "mv evol1_filtered.vcf.gz.tbi evol1_subtracted_anc.vcf.gz.tbi" not in repaired_command
    assert "rm -rf .isec_export_evol1_subtracted_anc" in repaired_command


def test_repair_bcftools_isec_command_handles_fix14_direct_output_chain() -> None:
    command = (
        "set -euo pipefail && mkdir -p /tmp/selected/variants && "
        "bcftools isec -C -w1 /tmp/selected/variants/evol2.filtered.vcf.gz "
        "/tmp/selected/variants/anc.filtered.vcf.gz -Oz -o "
        "/tmp/selected/variants/evol2.ancestor_subtracted.vcf.gz && "
        "tabix -f -p vcf /tmp/selected/variants/evol2.ancestor_subtracted.vcf.gz"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "prefix_output_target_ignored" for issue in issues)
    assert repairs
    assert (
        "bcftools isec -C -w1 /tmp/selected/variants/evol2.filtered.vcf.gz "
        "/tmp/selected/variants/anc.filtered.vcf.gz -Oz -p "
        ".isec_export_evol2.ancestor_subtracted"
    ) in repaired_command
    assert (
        "mv -f .isec_export_evol2.ancestor_subtracted/0000.vcf.gz "
        "/tmp/selected/variants/evol2.ancestor_subtracted.vcf.gz"
    ) in repaired_command
    assert repaired_command.endswith(
        "&& tabix -f -p vcf /tmp/selected/variants/evol2.ancestor_subtracted.vcf.gz"
    )


def test_repair_bcftools_isec_command_repairs_reused_prefix_export_collisions() -> None:
    command = (
        "cd /tmp/selected && "
        "bcftools isec -C -w1 -p ./anc_minus filtered_evol1.vcf.gz filtered_ancestor.vcf.gz && "
        "bcftools isec -C -w1 -p ./anc_minus filtered_evol2.vcf.gz filtered_ancestor.vcf.gz && "
        "mv anc_minus/0000.vcf evol1_subtracted_anc.vcf && "
        "mv anc_minus/0001.vcf evol2_subtracted_anc.vcf && "
        "bgzip -f evol1_subtracted_anc.vcf && "
        "bgzip -f evol2_subtracted_anc.vcf && "
        "tabix -f evol1_subtracted_anc.vcf.gz && "
        "tabix -f evol2_subtracted_anc.vcf.gz"
    )

    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(repair["reason"] == "reused_prefix_export_collision" for repair in repairs)
    assert "bcftools isec -C -w1 -p .isec_export_evol1_subtracted_anc" in repaired_command
    assert "bcftools isec -C -w1 -p .isec_export_evol2_subtracted_anc" in repaired_command
    assert "mv anc_minus/0000.vcf evol1_subtracted_anc.vcf" not in repaired_command
    assert "mv anc_minus/0001.vcf evol2_subtracted_anc.vcf" not in repaired_command
    assert "bgzip -f evol1_subtracted_anc.vcf" in repaired_command
    assert "bgzip -f evol2_subtracted_anc.vcf" in repaired_command


def test_repair_bcftools_isec_command_canonicalizes_duplicate_unique_options() -> None:
    command = (
        "bcftools isec -w1 -p . "
        "/tmp/ancestor.filtered.vcf.gz /tmp/evol1.filtered.vcf.gz "
        "-n=+1 -w1 -Oz -o evol1_minus_anc.vcf.gz"
    )

    issues = inspect_bcftools_isec_command(command)
    repaired_command, repairs = repair_bcftools_isec_command(command)

    assert any(issue["reason"] == "duplicate_unique_option" for issue in issues)
    assert any(repair["reason"] == "duplicate_unique_option" for repair in repairs)
    assert repaired_command.count("-w1") == 1
    assert "mv -f .isec_export_evol1_minus_anc/0000.vcf.gz evol1_minus_anc.vcf.gz" in repaired_command


def test_repair_bcftools_isec_command_handles_fix24_duplicate_write_shape() -> None:
    command = (
        "cd /tmp/selected && bcftools isec -w1 -p . "
        "/tmp/selected/ancestor_call.filtered.vcf.gz /tmp/selected/evol1_call.filtered.vcf.gz "
        "-n=+1 -w1 -Oz -o evol1_minus_anc.vcf.gz && "
        "bcftools isec -w1 -p . "
        "/tmp/selected/ancestor_call.filtered.vcf.gz /tmp/selected/evol2_call.filtered.vcf.gz "
        "-n=+1 -w1 -Oz -o evol2_minus_anc.vcf.gz"
    )

    repaired_command, _ = repair_bcftools_isec_command(command)

    assert repaired_command.count("-w1") == 2
    assert repaired_command.count("bcftools isec -w1") == 0
    assert ".isec_export_evol1_minus_anc/0000.vcf.gz" in repaired_command
    assert ".isec_export_evol2_minus_anc/0000.vcf.gz" in repaired_command


def test_repair_invalid_bcftools_isec_bash_run_commands_handles_overbroad_move_export_shape() -> None:
    plan = {
        "plan": [
            {
                "step_id": 10,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools isec -C -w1 ancestor_filtered.vcf.gz evol1_filtered.vcf.gz -p . && "
                        "mv evol1_filtered.vcf.gz evol1_subtracted_anc.vcf.gz && "
                        "mv evol1_filtered.vcf.gz.tbi evol1_subtracted_anc.vcf.gz.tbi"
                    )
                },
            }
        ]
    }

    repaired, meta = repair_invalid_bcftools_isec_bash_run_commands(plan)

    assert meta["changed"] is True
    repaired_command = repaired["plan"][0]["arguments"]["command"]
    assert "bcftools isec -C -w1 ancestor_filtered.vcf.gz evol1_filtered.vcf.gz -p .isec_export_evol1_subtracted_anc" in repaired_command
    assert "bgzip -c .isec_export_evol1_subtracted_anc/0000.vcf > evol1_subtracted_anc.vcf.gz" in repaired_command
    assert "tabix -f -p vcf evol1_subtracted_anc.vcf.gz" in repaired_command
