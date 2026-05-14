from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.run_agent_e2e_runtime_repair_support import (
    AgentE2ERuntimeRepairSupportMixin,
    _repair_flye_resource_settings,
)


class _DummyRepairHarness(AgentE2ERuntimeRepairSupportMixin):
    def __init__(self) -> None:
        self.cfg = SimpleNamespace(selected_dir=Path("/tmp"))
        self.run = {
            "user_request": "Assemble these Oxford Nanopore reads into contigs.",
            "failure_signatures": ["flye_out_of_memory", "flye_zero_coverage_estimate"],
            "plan": {
                "plan": [
                    {
                        "tool_name": "flye_assemble",
                        "step_id": 1,
                        "arguments": {
                            "reads_fastq": "/tmp/reads.fastq",
                            "threads": 4,
                            "output_dir": "/tmp/out",
                            "genome_size": "5m",
                        },
                    }
                ]
            },
            "step_statuses": ["failed"],
            "next_step_idx": 0,
            "status": "failed",
            "error": "Step 1 (flye_assemble) failed with exit code 1.",
        }

    def _note_failure_signature(self, signature: str) -> None:
        existing = {str(item).strip().lower() for item in self.run.get("failure_signatures", []) if str(item).strip()}
        existing.add(str(signature).strip().lower())
        self.run["failure_signatures"] = sorted(existing)


def test_repair_flye_resource_settings_lowers_threads_and_generic_genome_size() -> None:
    patched_plan, meta = _repair_flye_resource_settings(
        {
            "plan": [
                {
                    "tool_name": "flye_assemble",
                    "step_id": 1,
                    "arguments": {
                        "reads_fastq": "/tmp/reads.fastq",
                        "threads": 4,
                        "output_dir": "/tmp/out",
                        "genome_size": "5m",
                    },
                }
            ]
        },
        failed_step_number=1,
        user_request="Assemble these Oxford Nanopore reads into contigs.",
    )

    assert meta["changed"] is True
    args = patched_plan["plan"][0]["arguments"]
    assert args["threads"] == 1
    assert args["genome_size"] == "500k"


def test_repair_flye_resource_settings_preserves_explicit_genome_size_request() -> None:
    patched_plan, meta = _repair_flye_resource_settings(
        {
            "plan": [
                {
                    "tool_name": "flye_assemble",
                    "step_id": 1,
                    "arguments": {
                        "reads_fastq": "/tmp/reads.fastq",
                        "threads": 4,
                        "output_dir": "/tmp/out",
                        "genome_size": "5m",
                    },
                }
            ]
        },
        failed_step_number=1,
        user_request="Assemble these Oxford Nanopore reads into contigs for an estimated 5m genome.",
    )

    assert meta["changed"] is True
    args = patched_plan["plan"][0]["arguments"]
    assert args["threads"] == 1
    assert args["genome_size"] == "5m"


def test_apply_flye_resource_signature_repair_replans_locally() -> None:
    harness = _DummyRepairHarness()

    repaired, details = harness._apply_flye_resource_signature_repair()

    assert repaired is True
    assert details["why"] == "signature_guided_flye_resource_repair"
    assert harness.run["status"] == "planned"
    assert harness.run["next_step_idx"] == 0
    args = harness.run["plan"]["plan"][0]["arguments"]
    assert args["threads"] == 1
    assert args["genome_size"] == "500k"


def test_apply_bcftools_expression_signature_repair_replans_locally(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=9\tDP\t9\n"
        ),
        encoding="utf-8",
    )

    harness = _DummyRepairHarness()
    harness.cfg = SimpleNamespace(selected_dir=selected_dir)
    harness.run = {
        "user_request": "Filter bacterial variants into a comparison-ready VCF.",
        "failure_signatures": ["bcftools_ambiguous_expression_namespace:dp"],
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "step_id": 1,
                    "arguments": {
                        "command": (
                            f"bcftools filter -e 'QUAL<30 || DP<5' "
                            f"-Oz -o {selected_dir / 'filtered.vcf.gz'} {input_vcf.name}"
                        )
                    },
                }
            ]
        },
        "step_statuses": ["failed"],
        "next_step_idx": 0,
        "status": "failed",
        "error": "Step 1 (bash_run) failed with exit code 1.",
    }

    repaired, details = harness._apply_bcftools_expression_signature_repair()

    assert repaired is True
    assert details["why"] == "signature_guided_bcftools_expression_namespace_repair"
    assert harness.run["status"] == "planned"
    assert "INFO/DP<5" in harness.run["plan"]["plan"][0]["arguments"]["command"]


def test_apply_bcftools_expression_signature_repair_handles_missing_format_af_field(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=AF,Number=1,Type=Float,Description=\"Allele frequency\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tAF=0.9\tDP\t9\n"
        ),
        encoding="utf-8",
    )

    harness = _DummyRepairHarness()
    harness.cfg = SimpleNamespace(selected_dir=selected_dir)
    harness.run = {
        "user_request": "Filter bacterial variants into a comparison-ready VCF.",
        "failure_signatures": ["bcftools_missing_expression_namespace_field:format:af"],
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "step_id": 9,
                    "arguments": {
                        "command": (
                            f"bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/AF>=0.8' "
                            f"-Oz -o {selected_dir / 'filtered.vcf.gz'} {input_vcf.name}"
                        )
                    },
                }
            ]
        },
        "step_statuses": ["completed"] * 8 + ["failed"],
        "next_step_idx": 8,
        "status": "failed",
        "error": "Step 9 (bash_run) failed with exit code 255.",
    }

    repaired, details = harness._apply_bcftools_expression_signature_repair()

    assert repaired is True
    assert details["why"] == "signature_guided_bcftools_expression_namespace_repair"
    command = harness.run["plan"]["plan"][0]["arguments"]["command"]
    assert "FORMAT/AF>=0.8" not in command
    assert "INFO/AF>=0.8" in command


def test_apply_snpeff_codon_table_signature_repair_clears_incompatible_override() -> None:
    harness = _DummyRepairHarness()
    harness.run = {
        "user_request": "Annotate evolved variants against the assembled ancestor reference.",
        "failure_signatures": ["snpeff_invalid_codon_table:11"],
        "plan": {
            "plan": [
                {
                    "tool_name": "snpeff_annotate",
                    "step_id": 10,
                    "arguments": {
                        "genome_db": "ancestor",
                        "reference_fasta": "/tmp/assembly/scaffolds.fa",
                        "annotation_gff": "/tmp/assembly/genes.gff",
                        "config_dir": "/tmp/out/_snpeff",
                        "input_vcf": "/tmp/out/evol1.ancestor_subtracted.vcf.gz",
                        "output_vcf": "/tmp/out/evol1.annotated.vcf",
                        "codon_table": "11",
                    },
                }
            ]
        },
        "step_statuses": ["failed"],
        "next_step_idx": 0,
        "status": "failed",
        "error": "Step 10 (snpeff_annotate) failed with exit code 255.",
    }

    repaired, details = harness._apply_snpeff_codon_table_signature_repair()

    assert repaired is True
    assert details["why"] == "signature_guided_snpeff_codon_table_repair"
    assert harness.run["status"] == "planned"
    assert harness.run["plan"]["plan"][0]["arguments"]["codon_table"] == ""


def test_apply_bcftools_view_cli_repair_replans_locally(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\t.\n"
        ),
        encoding="utf-8",
    )

    harness = _DummyRepairHarness()
    harness.cfg = SimpleNamespace(selected_dir=selected_dir)
    harness.run = {
        "user_request": "Filter bacterial variants into branch-specific VCFs.",
        "failure_signatures": [],
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "step_id": 9,
                    "arguments": {
                        "command": (
                            f"bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' "
                            f"-m -v snps,indels -Oz -o {selected_dir / 'ancestor_filtered.vcf.gz'} {input_vcf} && "
                            f"bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' "
                            f"-m -v snps,indels -Oz -o {selected_dir / 'evol1_filtered.vcf.gz'} {input_vcf}"
                        )
                    },
                }
            ]
        },
        "step_statuses": ["completed"] * 8 + ["failed"],
        "next_step_idx": 8,
        "status": "failed",
        "error": "Step 9 (bash_run) failed with exit code 255.",
    }

    repaired, details = harness._apply_bcftools_view_cli_repair()

    assert repaired is True
    assert details["why"] == "deterministic_bcftools_view_cli_repair"
    assert harness.run["status"] == "planned"
    command = harness.run["plan"]["plan"][0]["arguments"]["command"]
    assert "-m -v snps,indels" not in command
    assert "bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' -v snps,indels" in command


def test_apply_bcftools_view_cli_repair_handles_line_continuation_segments(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\t.\n"
        ),
        encoding="utf-8",
    )

    harness = _DummyRepairHarness()
    harness.cfg = SimpleNamespace(selected_dir=selected_dir)
    harness.run = {
        "user_request": "Filter bacterial variants into branch-specific VCFs.",
        "failure_signatures": [],
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "step_id": 9,
                    "arguments": {
                        "command": (
                            f"mkdir -p {selected_dir} && \\\n"
                            f"cd {selected_dir} && \\\n"
                            f"bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' "
                            f"-m -v snps,indels -Oz -o ancestor_filtered.vcf.gz {input_vcf.name} && \\\n"
                            "bcftools index -t ancestor_filtered.vcf.gz"
                        )
                    },
                }
            ]
        },
        "step_statuses": ["completed"] * 8 + ["failed"],
        "next_step_idx": 8,
        "status": "failed",
        "error": "Step 9 (bash_run) failed with exit code 255.",
    }

    repaired, details = harness._apply_bcftools_view_cli_repair()

    assert repaired is True
    assert details["why"] == "deterministic_bcftools_view_cli_repair"
    command = harness.run["plan"]["plan"][0]["arguments"]["command"]
    assert "\\\n" in command
    assert "-m -v snps,indels" not in command
    assert "bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' -v snps,indels" in command
