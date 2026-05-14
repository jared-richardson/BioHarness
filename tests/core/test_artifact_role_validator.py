"""Tests for bio_harness.core.artifact_role_validator."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.artifact_role_validator import (
    repair_artifact_role_violations,
    summarize_artifact_role_violations,
    validate_artifact_role_invariants,
)


def test_validate_artifact_role_invariants_flags_input_equals_output(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": str(selected_dir / "stringtie" / "assembled.gtf"),
                    "output_gtf": str(selected_dir / "stringtie" / "assembled.gtf"),
                    "gene_abundance_tsv": str(selected_dir / "stringtie" / "gene_abundances.tsv"),
                },
            }
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert summarize_artifact_role_violations(violations) == [
        (
            "stringtie_quant.annotation_gtf:input_equals_output:"
            f"{(selected_dir / 'stringtie' / 'assembled.gtf').resolve(strict=False)}"
        )
    ]


def test_repair_artifact_role_violations_restores_reference_input_from_source_plan(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    source_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                    "output_gtf": str(selected_dir / "stringtie" / "assembled.gtf"),
                    "gene_abundance_tsv": str(selected_dir / "stringtie" / "gene_abundances.tsv"),
                },
            }
        ]
    }
    corrupted_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": str(selected_dir / "stringtie" / "assembled.gtf"),
                    "output_gtf": str(selected_dir / "stringtie" / "assembled.gtf"),
                    "gene_abundance_tsv": str(selected_dir / "stringtie" / "gene_abundances.tsv"),
                },
            }
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        corrupted_plan,
        source_plan=source_plan,
        selected_dir=selected_dir,
    )

    assert meta["changed"] is True
    assert repaired["plan"][0]["arguments"]["annotation_gtf"] == "/tmp/refs/genes.gtf"
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_inferrs_upstream_selected_dir_alias_for_typed_input(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "output_dir": str(selected_dir / "ancestor_assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "ancestor_contigs.fasta"),
                    "output_gff": str(selected_dir / "annotation" / "genes.gff"),
                    "output_faa": str(selected_dir / "annotation" / "genes.faa"),
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    assert repaired["plan"][1]["arguments"]["input_fasta"] == str(
        (selected_dir / "ancestor_assembly" / "contigs.fasta").resolve(strict=False)
    )
    assert meta["inferred"] == ["prodigal_annotate.input_fasta"]
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_rewrites_bash_run_selected_dir_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz /refs/ancestor.vcf.gz && "
                        "bcftools view -Oz -o evol1_filtered.vcf.gz /refs/evol1.vcf.gz"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -C -w1 evol1_filtered.vcf.gz anc_filtered.vcf.gz "
                        "-Oz -o evol1_subtracted.vcf.gz"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][1]["arguments"]["command"]
    assert "anc_filtered.vcf.gz" not in repaired_command
    assert "ancestor_filtered.vcf.gz" in repaired_command
    assert "bash_run.command:anc_filtered.vcf.gz" in meta["inferred"]
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_prefers_filtered_aliases_for_isec_variant_names(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz /refs/ancestor.vcf.gz && "
                        "bcftools view -Oz -o evol1_filtered.vcf.gz /refs/evol1.vcf.gz"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -n=1 -w1 -Oz -o evol1_subtracted_anc.vcf.gz "
                        "evol1_variants.vcf.gz ancestor_variants.vcf.gz"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][1]["arguments"]["command"]
    assert "evol1_variants.vcf.gz" not in repaired_command
    assert "ancestor_variants.vcf.gz" not in repaired_command
    assert "evol1_filtered.vcf.gz" in repaired_command
    assert "ancestor_filtered.vcf.gz" in repaired_command
    assert meta["remaining_issues"] == []


def test_validate_artifact_role_invariants_ignores_shell_assignments_and_resolves_output_dir_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f'OUTPUT_DIR="{selected_dir}" && '
                        'bcftools view -Oz -o "$OUTPUT_DIR/ancestor_filtered.vcf.gz" /refs/ancestor.vcf.gz'
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f'OUTPUT_DIR="{selected_dir}" && '
                        'bcftools view -Oz -o "$OUTPUT_DIR/evol1_filtered.vcf.gz" '
                        '"$OUTPUT_DIR/ancestor_filtered.vcf.gz"'
                    )
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(plan, selected_dir=selected_dir)

    assert summarize_artifact_role_violations(violations) == []


def test_validate_artifact_role_invariants_resolves_chained_shell_alias_into_typed_wrapper_input(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f'OUTPUT_DIR="{selected_dir}" && '
                        'EVOL2_SUBTRACTED_VCF="$OUTPUT_DIR/evol2_subtracted.vcf.gz" && '
                        'bcftools view -Oz -o "$EVOL2_SUBTRACTED_VCF" /refs/evol2.vcf.gz'
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "${EVOL2_SUBTRACTED_VCF}",
                    "output_vcf": str(selected_dir / "evol2_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(plan, selected_dir=selected_dir)

    assert summarize_artifact_role_violations(violations) == []


def test_validate_artifact_role_invariants_ignores_function_local_vars_and_output_roots(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": str(selected_dir / "ancestor_variants.vcf"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol1.bam",
                    "output_vcf": str(selected_dir / "evol1_variants.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol2.bam",
                    "output_vcf": str(selected_dir / "evol2_variants.vcf"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "set -euo pipefail\n"
                        f'OUTPUT_DIR="{selected_dir}"\n'
                        'ANCESTOR_VCF="${OUTPUT_DIR}/ancestor_variants.vcf"\n'
                        'EVOL1_VCF="${OUTPUT_DIR}/evol1_variants.vcf"\n'
                        'EVOL2_VCF="${OUTPUT_DIR}/evol2_variants.vcf"\n'
                        "process_vcf() {\n"
                        '  local input_vcf="$1"\n'
                        '  local output_vcf="$2"\n'
                        '  if [ ! -f "${input_vcf}" ]; then\n'
                        '    echo "missing ${input_vcf}"\n'
                        "    exit 1\n"
                        "  fi\n"
                        '  bgzip -f "${output_vcf}.tmp"\n'
                        '  tabix -p vcf "${output_vcf}.gz"\n'
                        "}\n"
                        'process_vcf "${ANCESTOR_VCF}" "${OUTPUT_DIR}/ancestor_variants_filtered"\n'
                        'process_vcf "${EVOL1_VCF}" "${OUTPUT_DIR}/evol1_variants_filtered"\n'
                        'process_vcf "${EVOL2_VCF}" "${OUTPUT_DIR}/evol2_variants_filtered"\n'
                    )
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(plan, selected_dir=selected_dir)

    assert summarize_artifact_role_violations(violations) == []


def test_validate_artifact_role_invariants_flags_unresolved_shell_variable_wrapper_path(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "${EVOL2_SUBTRACTED_VCF}",
                    "output_vcf": str(selected_dir / "evol2_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(plan, selected_dir=selected_dir)

    assert summarize_artifact_role_violations(violations) == [
        "snpeff_annotate.input_vcf:unresolved_shell_variable_path:${EVOL2_SUBTRACTED_VCF}"
    ]


def test_validate_artifact_role_invariants_ignores_heredoc_python_output_literals(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/refs/evol1.vcf.gz",
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                    "genome_db": "ancestor",
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/refs/evol2.vcf.gz",
                    "output_vcf": str(selected_dir / "evol2_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                    "genome_db": "ancestor",
                },
            },
            {
                "step_id": 3,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && python3 << 'EOF'\n"
                        "evol1_vcf = 'evol1_annotated.vcf'\n"
                        "evol2_vcf = 'evol2_annotated.vcf'\n"
                        "output_csv = 'variants_shared.csv'\n"
                        "with open(output_csv, 'w', encoding='utf-8') as handle:\n"
                        "    handle.write('CHROM\\n')\n"
                        "EOF"
                    )
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(plan, selected_dir=selected_dir)

    assert summarize_artifact_role_violations(violations) == []


def test_validate_artifact_role_invariants_flags_unsupported_shell_variable_expansion(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "${OUTPUT_DIR:-/tmp}/evol2.vcf.gz",
                    "output_vcf": str(selected_dir / "evol2_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(plan, selected_dir=selected_dir)

    assert summarize_artifact_role_violations(violations) == [
        "snpeff_annotate.input_vcf:unsupported_shell_variable_expansion:${OUTPUT_DIR:-/tmp}/evol2.vcf.gz"
    ]


def test_repair_artifact_role_violations_prefers_branch_specific_raw_vcf_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": "spades_ancestor/anc_raw.vcf",
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol1.bam",
                    "output_vcf": "spades_ancestor/evol1_raw.vcf",
                },
            },
            {
                "step_id": 3,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz ancestor_raw.vcf.gz"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][2]["arguments"]["command"]
    assert "spades_ancestor/anc_raw.vcf" in repaired_command
    assert "spades_ancestor/evol1_raw.vcf" not in repaired_command
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_matches_minus_ancestor_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -w1 -n=+1 evol1_filtered.vcf.gz /refs/ancestor_filtered.vcf.gz "
                        "-Oz -o evol1_minus_anc.vcf.gz"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "evol1_subtracted_anc.vcf.gz"),
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    assert repaired["plan"][1]["arguments"]["input_vcf"] == str(
        (selected_dir / "evol1_minus_anc.vcf.gz").resolve(strict=False)
    )
    assert meta["inferred"] == ["snpeff_annotate.input_vcf"]
    assert meta["remaining_issues"] == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{(selected_dir / 'evol1_filtered.vcf.gz').resolve(strict=False)}"
        )
    ]


def test_repair_artifact_role_violations_matches_abbreviated_ancestor_sub_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -w1 -n=+1 evol1_filtered.vcf.gz /refs/ancestor_filtered.vcf.gz "
                        "-Oz -o evol1_ancestor_sub.vcf.gz"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "evol1_annotate_subtracted_anc.vcf.gz"),
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    assert repaired["plan"][1]["arguments"]["input_vcf"] == str(
        (selected_dir / "evol1_ancestor_sub.vcf.gz").resolve(strict=False)
    )
    assert meta["inferred"] == ["snpeff_annotate.input_vcf"]
    assert meta["remaining_issues"] == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{(selected_dir / 'evol1_filtered.vcf.gz').resolve(strict=False)}"
        )
    ]


def test_repair_artifact_role_violations_prefers_exact_suffix_for_subtracted_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -C -w1 evol1_filtered.vcf.gz ancestor_filtered.vcf.gz -p tmp_evol1_sub && "
                        "mv tmp_evol1_sub/0000.vcf evol1_subtracted_anc.vcf && "
                        "bgzip -f evol1_subtracted_anc.vcf && "
                        "tabix -p vcf evol1_subtracted_anc.vcf.gz"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "evol1_annotate_subtracted_anc.vcf.gz"),
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    assert repaired["plan"][1]["arguments"]["input_vcf"] == str(
        (selected_dir / "evol1_subtracted_anc.vcf.gz").resolve(strict=False)
    )
    assert meta["inferred"] == [
        "bash_run.command:bcftools_isec_output_mode",
        "snpeff_annotate.input_vcf",
    ]
    assert meta["remaining_issues"] == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{(selected_dir / 'evol1_filtered.vcf.gz').resolve(strict=False)}"
        ),
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{(selected_dir / 'ancestor_filtered.vcf.gz').resolve(strict=False)}"
        ),
    ]


def test_repair_artifact_role_violations_matches_generic_branch_raw_vcf_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": str(selected_dir / "ancestor_call" / "anc_raw.vcf"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol1.bam",
                    "output_vcf": str(selected_dir / "evol1_call" / "evol1_raw.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol2.bam",
                    "output_vcf": str(selected_dir / "evol2_call" / "evol2_raw.vcf"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz ancestor.vcf.gz && "
                        "bcftools view -Oz -o evol1_filtered.vcf.gz evolved1.vcf.gz && "
                        "bcftools view -Oz -o evol2_filtered.vcf.gz evolved2.vcf.gz"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][3]["arguments"]["command"]
    assert "ancestor_call/anc_raw.vcf" in repaired_command
    assert "evol1_call/evol1_raw.vcf" in repaired_command
    assert "evol2_call/evol2_raw.vcf" in repaired_command
    assert "ancestor.vcf.gz" not in repaired_command
    assert "evolved1.vcf.gz" not in repaired_command
    assert "evolved2.vcf.gz" not in repaired_command
    assert meta["inferred"] == [
        "bash_run.command:ancestor.vcf.gz",
        "bash_run.command:evolved1.vcf.gz",
        "bash_run.command:evolved2.vcf.gz",
    ]
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_matches_filtered_branch_vcf_predecessors(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": str(selected_dir / "ancestor_call" / "ancestor_raw.vcf"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol1.bam",
                    "output_vcf": str(selected_dir / "evol1_call" / "evol1_raw.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol2.bam",
                    "output_vcf": str(selected_dir / "evol2_call" / "evol2_raw.vcf"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -c 1 -Oz -o filtered_ancestor.vcf.gz filtered_ancestor.vcf && "
                        "bcftools view -c 1 -Oz -o filtered_evol1.vcf.gz filtered_evol1.vcf && "
                        "bcftools view -c 1 -Oz -o filtered_evol2.vcf.gz filtered_evol2.vcf"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][3]["arguments"]["command"]
    assert "filtered_ancestor.vcf.gz" in repaired_command
    assert "filtered_evol1.vcf.gz" in repaired_command
    assert "filtered_evol2.vcf.gz" in repaired_command
    assert "ancestor_call/ancestor_raw.vcf" in repaired_command
    assert "evol1_call/evol1_raw.vcf" in repaired_command
    assert "evol2_call/evol2_raw.vcf" in repaired_command
    assert "filtered_ancestor.vcf &&" not in repaired_command
    assert "filtered_evol1.vcf &&" not in repaired_command
    assert repaired_command.endswith("evol2_call/evol2_raw.vcf")
    assert meta["inferred"] == [
        "bash_run.command:filtered_ancestor.vcf",
        "bash_run.command:filtered_evol1.vcf",
        "bash_run.command:filtered_evol2.vcf",
    ]
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_repairs_reused_isec_prefix_export_collisions(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": str(selected_dir / "ancestor_raw.vcf"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol1.bam",
                    "output_vcf": str(selected_dir / "evol1_raw.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol2.bam",
                    "output_vcf": str(selected_dir / "evol2_raw.vcf"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -c1 -Oz -o filtered_ancestor.vcf.gz filtered_ancestor.vcf && "
                        "bcftools index filtered_ancestor.vcf.gz && "
                        "bcftools view -c1 -Oz -o filtered_evol1.vcf.gz filtered_evol1.vcf && "
                        "bcftools index filtered_evol1.vcf.gz && "
                        "bcftools view -c1 -Oz -o filtered_evol2.vcf.gz filtered_evol2.vcf && "
                        "bcftools index filtered_evol2.vcf.gz"
                    )
                },
            },
            {
                "step_id": 5,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -C -w1 -p ./anc_minus filtered_evol1.vcf.gz filtered_ancestor.vcf.gz && "
                        "bcftools isec -C -w1 -p ./anc_minus filtered_evol2.vcf.gz filtered_ancestor.vcf.gz && "
                        "mv anc_minus/0000.vcf evol1_subtracted_anc.vcf && "
                        "mv anc_minus/0001.vcf evol2_subtracted_anc.vcf && "
                        "bgzip -f evol1_subtracted_anc.vcf && "
                        "bgzip -f evol2_subtracted_anc.vcf && "
                        "tabix -f evol1_subtracted_anc.vcf.gz && "
                        "tabix -f evol2_subtracted_anc.vcf.gz"
                    )
                },
            },
            {
                "step_id": 6,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "evol1_annotate_subtracted_anc.vcf.gz"),
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
            {
                "step_id": 7,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "evol2_annotate_subtracted_anc.vcf.gz"),
                    "output_vcf": str(selected_dir / "evol2_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    step4_command = repaired["plan"][3]["arguments"]["command"]
    assert "ancestor_raw.vcf" in step4_command
    assert "evol1_raw.vcf" in step4_command
    assert "evol2_raw.vcf" in step4_command
    assert "filtered_ancestor.vcf.gz" in step4_command
    assert "filtered_evol1.vcf.gz" in step4_command
    assert "filtered_evol2.vcf.gz" in step4_command

    step5_command = repaired["plan"][4]["arguments"]["command"]
    assert "bcftools isec -C -w1 -p .isec_export_evol1_subtracted_anc" in step5_command
    assert "bcftools isec -C -w1 -p .isec_export_evol2_subtracted_anc" in step5_command
    assert "mv anc_minus/0000.vcf evol1_subtracted_anc.vcf" not in step5_command
    assert "mv anc_minus/0001.vcf evol2_subtracted_anc.vcf" not in step5_command

    assert repaired["plan"][5]["arguments"]["input_vcf"] == str(
        (selected_dir / "evol1_subtracted_anc.vcf.gz").resolve(strict=False)
    )
    assert repaired["plan"][6]["arguments"]["input_vcf"] == str(
        (selected_dir / "evol2_subtracted_anc.vcf.gz").resolve(strict=False)
    )
    assert "bash_run.command:bcftools_isec_output_mode" in meta["inferred"]
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_matches_no_ancestor_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -w1 -n=+1 evol1_filtered.vcf.gz /refs/ancestor_filtered.vcf.gz "
                        "-Oz -o evolved1_no_anc.vcf.gz"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "evol1_subtracted_anc.vcf.gz"),
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    assert repaired["plan"][1]["arguments"]["input_vcf"] == str(
        (selected_dir / "evolved1_no_anc.vcf.gz").resolve(strict=False)
    )
    assert meta["inferred"] == ["snpeff_annotate.input_vcf"]
    assert meta["remaining_issues"] == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{(selected_dir / 'evol1_filtered.vcf.gz').resolve(strict=False)}"
        )
    ]


def test_validate_artifact_role_invariants_allows_mv_bgzip_handoff_for_downstream_consumer(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": str(selected_dir / "ancestor_call" / "anc_raw.vcf"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz ancestor_call/anc_raw.vcf"
                    )
                },
            },
            {
                "step_id": 3,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol2.bam",
                    "output_vcf": str(selected_dir / "evol2_call" / "evol2_raw.vcf"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -p evol2_minus_anc evol2_call/evol2_raw.vcf ancestor_filtered.vcf.gz && "
                        "mv evol2_minus_anc/0002.vcf evol2_subtracted_anc.vcf && "
                        "bgzip -f evol2_subtracted_anc.vcf && "
                        "tabix -f -p vcf evol2_subtracted_anc.vcf.gz"
                    )
                },
            },
            {
                "step_id": 5,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "evol2_subtracted_anc.vcf.gz"),
                    "output_vcf": str(selected_dir / "evol2_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert summarize_artifact_role_violations(violations) == []


def test_repair_artifact_role_violations_matches_call_vcf_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": str(selected_dir / "ancestor_call" / "ancestor.raw.vcf"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol1.bam",
                    "output_vcf": str(selected_dir / "evol1_call" / "evol1.raw.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz ancestor_call.vcf && "
                        "bcftools view -Oz -o evol1_filtered.vcf.gz evol1_call.vcf"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][2]["arguments"]["command"]
    assert "ancestor_call/ancestor.raw.vcf" in repaired_command
    assert "evol1_call/evol1.raw.vcf" in repaired_command
    assert meta["inferred"] == [
        "bash_run.command:ancestor_call.vcf",
        "bash_run.command:evol1_call.vcf",
    ]
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_matches_caller_named_raw_vcf_aliases(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/anc.bam",
                    "output_vcf": str(selected_dir / "ancestor_variant_calling" / "anc_raw.vcf"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol1.bam",
                    "output_vcf": str(selected_dir / "evol1_variants" / "raw_evol1.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": "/inputs/evol2.bam",
                    "output_vcf": str(selected_dir / "evol2_variant_calling" / "evol2_raw.vcf"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o filtered_anc.vcf.gz ancestor_freebayes.vcf && "
                        "bcftools view -Oz -o filtered_evol1.vcf.gz evol1_freebayes.vcf && "
                        f"bcftools view -Oz -o filtered_evol2.vcf.gz {selected_dir / 'evol2_freebayes.vcf'}"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][3]["arguments"]["command"]
    assert "anc_raw.vcf" in repaired_command
    assert "raw_evol1.vcf" in repaired_command
    assert str((selected_dir / "evol2_variant_calling" / "evol2_raw.vcf").resolve(strict=False)) in repaired_command
    assert "ancestor_freebayes.vcf" not in repaired_command
    assert "evol1_freebayes.vcf" not in repaired_command
    assert str((selected_dir / "evol2_freebayes.vcf").resolve(strict=False)) not in repaired_command
    assert sorted(meta["inferred"]) == [
        "bash_run.command:ancestor_freebayes.vcf",
        "bash_run.command:evol1_freebayes.vcf",
        "bash_run.command:evol2_freebayes.vcf",
    ]
    assert meta["remaining_issues"] == []


def test_repair_artifact_role_violations_preserves_compound_vcf_stages(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    evol1_filtered = selected_dir / "evol1_annotated_filtered.vcf.gz"
    evol2_filtered = selected_dir / "evol2_annotated_filtered.vcf.gz"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/refs/evol1_minus_anc.vcf.gz",
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -C -n=2 -w1 "
                        "evol1_annotated_filtered.vcf.gz evol2_annotated_filtered.vcf.gz "
                        "-p /tmp/shared_vcf"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    repaired_command = repaired["plan"][1]["arguments"]["command"]
    assert "evol1_annotated_filtered.vcf.gz" in repaired_command
    remaining = validate_artifact_role_invariants(
        repaired,
        selected_dir=selected_dir,
    )
    assert summarize_artifact_role_violations(remaining) == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{evol1_filtered.resolve(strict=False)}"
        ),
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{evol2_filtered.resolve(strict=False)}"
        ),
    ]


def test_validate_artifact_role_invariants_allows_upstream_bash_output_for_downstream_profile(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    final_csv = selected_dir / "final" / "pathway_comparison.csv"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python compare_pathways.py "
                        f"--output-csv {final_csv} "
                        f"--output_dir {selected_dir / 'output'}"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "artifact_schema_profile",
                "arguments": {
                    "input_path": str(final_csv),
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert violations == []


def test_validate_artifact_role_invariants_allows_bash_run_selected_output_with_shell_bindings(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    annotated_vcf = selected_dir / "evol1_annotated.vcf.gz"
    shared_csv = selected_dir / "shared_variants.csv"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/inputs/evol1_subtracted_anc.vcf.gz",
                    "output_vcf": str(annotated_vcf),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f'OUTPUT_DIR="{selected_dir}" && '
                        f'ANNOTATED_VCF="{annotated_vcf}" && '
                        'FINAL_CSV="${OUTPUT_DIR}/shared_variants.csv" && '
                        'python3 export_shared_variants_csv.py '
                        '--input-vcf "${ANNOTATED_VCF}" '
                        '--output-csv "${FINAL_CSV}"'
                    )
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert violations == []
    assert shared_csv == selected_dir / "shared_variants.csv"


def test_validate_artifact_role_invariants_flags_bash_run_selected_input_without_producer(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    missing_input = selected_dir / "ancestor_call" / "anc_raw.vcf"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir} && "
                        f"cd {selected_dir} && "
                        "bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' "
                        "-Oz -o ancestor_call/anc_filtered.vcf.gz ancestor_call/anc_raw.vcf && "
                        "bcftools index -f ancestor_call/anc_filtered.vcf.gz"
                    )
                },
            }
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert summarize_artifact_role_violations(violations) == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{missing_input.resolve(strict=False)}"
        )
    ]


def test_validate_artifact_role_invariants_flags_positional_inputs_after_self_contained_flag(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    evol1_missing = selected_dir / "evol1_annotated_filtered.vcf.gz"
    evol2_missing = selected_dir / "evol2_annotated_filtered.vcf.gz"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -C -n=2 -w1 "
                        "evol1_annotated_filtered.vcf.gz evol2_annotated_filtered.vcf.gz "
                        "-p /tmp/shared_vcf"
                    )
                },
            }
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert summarize_artifact_role_violations(violations) == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{evol1_missing.resolve(strict=False)}"
        ),
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{evol2_missing.resolve(strict=False)}"
        ),
    ]


def test_validate_artifact_role_invariants_flags_inputs_hidden_by_bcftools_isec_selected_root(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    evol1_missing = selected_dir / "evol1.filtered.vcf.gz"
    anc_missing = selected_dir / "anc.filtered.vcf.gz"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -C -w1 -p . "
                        "evol1.filtered.vcf.gz anc.filtered.vcf.gz "
                        "-Oz -o evol1_no_anc.vcf.gz"
                    )
                },
            }
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert summarize_artifact_role_violations(violations) == [
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{evol1_missing.resolve(strict=False)}"
        ),
        (
            "bash_run.command:input_in_selected_dir_without_producer:"
            f"{anc_missing.resolve(strict=False)}"
        ),
    ]


def test_repair_artifact_role_violations_rewrites_bcftools_isec_alias_inputs(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o evol1_raw.filtered.vcf.gz evol1_call/evol1_raw.vcf && "
                        "bcftools view -Oz -o anc_background.filtered.vcf.gz ancestor_call/anc.raw.vcf"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools isec -C -w1 -p . "
                        "evol1.filtered.vcf.gz anc.filtered.vcf.gz "
                        "-Oz -o evol1_no_anc.vcf.gz"
                    )
                },
            },
        ]
    }

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
    )

    assert meta["changed"] is True
    command = repaired["plan"][1]["arguments"]["command"]
    assert "evol1_raw.filtered.vcf.gz" in command
    assert "anc_background.filtered.vcf.gz" in command


def test_validate_artifact_role_invariants_allows_same_step_bash_output_reuse(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir} && "
                        f"cd {selected_dir} && "
                        "samtools sort -o alignments/sample.sorted.bam /inputs/sample.bam && "
                        "samtools index alignments/sample.sorted.bam"
                    )
                },
            }
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert violations == []


def test_validate_artifact_role_invariants_allows_same_command_outputs_with_comments(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/refs/evol1.vcf.gz",
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/refs/evol2.vcf.gz",
                    "output_vcf": str(selected_dir / "evol2_annotated.vcf"),
                    "reference_fasta": "/refs/ancestor.fa",
                    "annotation_gff": "/refs/ancestor.gff",
                },
            },
            {
                "step_id": 3,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && \\\n"
                        "# Filter both evolved lines for high/moderate impact variants and intersect them\n"
                        "SnpSift filter \"(ANN[*].IMPACT = 'HIGH') || (ANN[*].IMPACT = 'MODERATE')\" "
                        "evol1_annotated.vcf > shared_variants/evol1_high_mod.vcf && \\\n"
                        "SnpSift filter \"(ANN[*].IMPACT = 'HIGH') || (ANN[*].IMPACT = 'MODERATE')\" "
                        "evol2_annotated.vcf > shared_variants/evol2_high_mod.vcf && \\\n"
                        "bcftools isec -w1 -n=2 shared_variants/evol1_high_mod.vcf "
                        "shared_variants/evol2_high_mod.vcf -p shared_variants/isec && \\\n"
                        "bcftools query -f '%CHROM\\n' shared_variants/isec/0000.vcf > "
                        "shared_variants/shared_high_mod_variants.csv"
                    )
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert violations == []


def test_repair_artifact_role_violations_rebinds_deterministic_isec_consumers(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz /refs/ancestor_raw.vcf.gz && "
                        "bcftools view -Oz -o evol1_filtered.vcf.gz /refs/evol1_raw.vcf.gz && "
                        "bcftools isec -n=2 -w1 evol1_filtered.vcf.gz ancestor_filtered.vcf.gz -p isec_dir1"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ecoli_custom",
                    "input_vcf": str(selected_dir / "isec_dir1" / "0002.vcf"),
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "annotation_gff": str(data_root / "genes.gff"),
                    "reference_fasta": str(data_root / "scaffolds.fasta"),
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
        allowed_input_roots=[data_root],
    )

    assert summarize_artifact_role_violations(violations) == [
        f"snpeff_annotate.input_vcf:input_in_selected_dir_without_producer:{selected_dir / 'isec_dir1' / '0002.vcf'}"
    ]

    repaired, meta = repair_artifact_role_violations(
        plan,
        source_plan=plan,
        selected_dir=selected_dir,
        allowed_input_roots=[data_root],
    )

    assert meta["changed"] is True
    assert repaired["plan"][1]["arguments"]["input_vcf"] == str(selected_dir / "isec_dir1" / "0000.vcf")
    assert validate_artifact_role_invariants(
        repaired,
        selected_dir=selected_dir,
        allowed_input_roots=[data_root],
    ) == []


def test_validate_artifact_role_invariants_allows_upstream_bash_output_prefix_for_downstream_vcf(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    intersect_root = selected_dir / "filtered" / "intersected"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o calls/evol1_raw.vcf /refs/evol1_raw.vcf.gz"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o filtered/anc_filtered.vcf.gz /refs/anc_filtered.vcf.gz"
                    )
                },
            },
            {
                "step_id": 3,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o filtered/evol1_filtered.vcf.gz calls/evol1_raw.vcf && "
                        "bcftools isec -w1 -p filtered/intersected "
                        "filtered/evol1_filtered.vcf.gz filtered/anc_filtered.vcf.gz"
                    )
                },
            },
            {
                "step_id": 4,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ecoli_custom",
                    "input_vcf": str(intersect_root / "0000.vcf"),
                    "output_vcf": str(selected_dir / "annotated" / "evol1.vcf"),
                    "annotation_gff": "/tmp/refs/genes.gff",
                    "reference_fasta": "/tmp/refs/contigs.fasta",
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert violations == []


def test_validate_artifact_role_invariants_allows_upstream_normalized_featurecounts_gff(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    normalized_gff = selected_dir / "references" / "annotation_for_featurecounts.gff"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 pipeline_scripts/normalize_gff_for_featurecounts.py "
                        "/refs/genes.gff "
                        f"{normalized_gff}"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "subread_align",
                "arguments": {
                    "index_base": str(selected_dir / "subread_index" / "genome"),
                    "reference_fasta": "/refs/genome.fa",
                    "reads_1": "/inputs/sample_R1.fastq.gz",
                    "reads_2": "/inputs/sample_R2.fastq.gz",
                    "output_bam": str(selected_dir / "alignments" / "sample.bam"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "featurecounts_run",
                "arguments": {
                    "input_bams": [str(selected_dir / "alignments" / "sample.bam")],
                    "annotation_gtf": str(normalized_gff),
                    "annotation_format": "GFF",
                    "feature_type": "gene",
                    "attribute_type": "ID",
                    "output_counts": str(selected_dir / "counts" / "gene_counts.txt"),
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert violations == []


def test_validate_artifact_role_invariants_allows_featurecounts_bam_string_from_star_prefix_outputs(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    sample_one_bam = selected_dir / "SRR1278968_Aligned.out.bam"
    sample_two_bam = selected_dir / "SRR1278969_Aligned.out.bam"
    count_matrix = selected_dir / "featurecounts_output" / "counts_matrix.tsv"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "star_align",
                "arguments": {
                    "genome_dir": "/refs/star_index",
                    "reads_1": "/inputs/SRR1278968_1.fastq.gz",
                    "reads_2": "/inputs/SRR1278968_2.fastq.gz",
                    "annotation_gtf": "/refs/genes.gtf",
                    "output_prefix": str(selected_dir / "SRR1278968_"),
                    "threads": 8,
                },
            },
            {
                "step_id": 2,
                "tool_name": "star_align",
                "arguments": {
                    "genome_dir": "/refs/star_index",
                    "reads_1": "/inputs/SRR1278969_1.fastq.gz",
                    "reads_2": "/inputs/SRR1278969_2.fastq.gz",
                    "annotation_gtf": "/refs/genes.gtf",
                    "output_prefix": str(selected_dir / "SRR1278969_"),
                    "threads": 8,
                },
            },
            {
                "step_id": 3,
                "tool_name": "featurecounts_run",
                "arguments": {
                    "input_bams": f"{sample_one_bam} {sample_two_bam}",
                    "annotation_gtf": "/refs/genes.gtf",
                    "output_counts": str(count_matrix),
                    "threads": 8,
                },
            },
            {
                "step_id": 4,
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": str(count_matrix),
                    "metadata_table": "/refs/sample_metadata.tsv",
                    "design_formula": "~ condition",
                    "contrast": "condition,treatment,control",
                    "output_dir": str(selected_dir / "deseq2_output"),
                },
            },
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
    )

    assert violations == []


def test_validate_artifact_role_invariants_allows_readonly_data_root_under_selected_dir(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(data_root / "ref.fa"),
                    "reads_1": str(data_root / "sample_R1.fastq.gz"),
                    "reads_2": str(data_root / "sample_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments" / "sample.bam"),
                },
            }
        ]
    }

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
        allowed_input_roots=[data_root],
    )

    assert violations == []
