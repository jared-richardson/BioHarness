from __future__ import annotations

import pytest

from bio_harness.skills.library.flye_assemble import flye_assemble
from bio_harness.skills.library.limma_voom_run import limma_voom_run
from bio_harness.skills.library.macs2_atacseq_callpeak import macs2_atacseq_callpeak
from bio_harness.skills.library.macs2_chipseq_callpeak import macs2_chipseq_callpeak
from bio_harness.skills.library.mafft_align import mafft_align
from bio_harness.skills.library.majiq_run import majiq_run
from bio_harness.skills.library.seurat_rscript_workflow import seurat_rscript_workflow
from bio_harness.skills.library.sniffles_sv_call import sniffles_sv_call
from bio_harness.skills.library.trinity_assemble import trinity_assemble
from bio_harness.skills.library.vep_annotate import vep_annotate


def test_flye_assemble_renders_expected_command() -> None:
    cmd = flye_assemble(
        reads_fastq="/tmp/long.fastq.gz",
        threads=8,
        output_dir="/tmp/flye",
        genome_size="4.8m",
    )

    assert "set -euo pipefail;" in cmd
    assert "flye --nano-raw /tmp/long.fastq.gz" in cmd
    assert "--threads 8" in cmd
    assert "--out-dir /tmp/flye" in cmd
    assert "--genome-size 4.8m" in cmd


def test_flye_assemble_prefers_launcher_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.flye_assemble.tool_launcher_command",
        lambda name: "/opt/tool-envs/flye/bin/flye" if name == "flye" else None,
    )
    cmd = flye_assemble(
        reads_fastq="/tmp/long.fastq.gz",
        threads=2,
        output_dir="/tmp/flye",
        genome_size="12k",
    )

    assert "/opt/tool-envs/flye/bin/flye --nano-raw /tmp/long.fastq.gz" in cmd


def test_flye_assemble_supports_meta_mode_and_read_mode() -> None:
    cmd = flye_assemble(
        reads_fastq="/tmp/long.fastq.gz",
        threads=2,
        output_dir="/tmp/flye_meta",
        genome_size="100k",
        read_mode="pacbio-hifi",
        meta_mode=True,
    )

    assert "flye --pacbio-hifi /tmp/long.fastq.gz --meta" in cmd
    assert "--threads 2" in cmd
    assert "--genome-size 100k" in cmd


def test_flye_assemble_without_meta_mode_does_not_render_empty_argument() -> None:
    cmd = flye_assemble(
        reads_fastq="/tmp/long.fastq.gz",
        threads=2,
        output_dir="/tmp/flye_out",
        genome_size="5m",
        read_mode="nano-raw",
        meta_mode=False,
    )

    assert "''" not in cmd
    assert " --meta " not in cmd
    assert "flye --nano-raw /tmp/long.fastq.gz --threads 2" in cmd


def test_limma_voom_run_renders_expected_command() -> None:
    cmd = limma_voom_run(
        counts_matrix="/tmp/counts.tsv",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ condition",
        contrast="condition_treated_vs_control",
        output_dir="/tmp/limma_out",
    )

    assert "limma_voom_wrapper.R" in cmd
    assert "--counts /tmp/counts.tsv" in cmd
    assert "--metadata /tmp/meta.tsv" in cmd
    assert "--design '~ condition'" in cmd


def test_macs2_chipseq_callpeak_renders_expected_command() -> None:
    cmd = macs2_chipseq_callpeak(
        treatment_bam="/tmp/treat.bam",
        control_bam="/tmp/control.bam",
        genome_size="hs",
        name="chipseq_test",
        output_dir="/tmp/macs2_out",
    )

    assert "macs2 callpeak" in cmd
    assert cmd.startswith("mkdir -p /tmp/macs2_out && ")
    assert "-t /tmp/treat.bam" in cmd
    assert "-c /tmp/control.bam" in cmd
    assert "-g hs" in cmd


def test_macs2_atacseq_callpeak_renders_expected_command() -> None:
    cmd = macs2_atacseq_callpeak(
        treatment_bam="/tmp/atac.bam",
        genome_size="hs",
        name="atac_test",
        output_dir="/tmp/macs2_atac_out",
    )

    assert "macs2 callpeak" in cmd
    assert cmd.startswith("mkdir -p /tmp/macs2_atac_out && ")
    assert "-t /tmp/atac.bam" in cmd
    assert "-f BAMPE" in cmd
    assert "--nomodel --shift -100 --extsize 200" in cmd


def test_mafft_align_renders_expected_command() -> None:
    cmd = mafft_align(
        input_fasta="/tmp/input.fa",
        output_fasta="/tmp/phylo/aligned.fa",
        threads=4,
        strategy_mode="auto",
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/phylo;" in cmd
    assert "--auto --thread 4 /tmp/input.fa > /tmp/phylo/aligned.fa" in cmd


def test_mafft_align_prefers_launcher_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.mafft_align.tool_launcher_command",
        lambda name: "/opt/tool-envs/mafft/bin/mafft" if name == "mafft" else None,
    )

    cmd = mafft_align(
        input_fasta="/tmp/input.fa",
        output_fasta="/tmp/phylo/aligned.fa",
        threads=1,
        strategy_mode="globalpair",
    )

    assert "/opt/tool-envs/mafft/bin/mafft --globalpair --thread 1 /tmp/input.fa > /tmp/phylo/aligned.fa" in cmd


def test_mafft_align_rejects_invalid_strategy_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported MAFFT strategy_mode"):
        mafft_align(
            input_fasta="/tmp/input.fa",
            output_fasta="/tmp/phylo/aligned.fa",
            strategy_mode="invalid",
        )


def test_sniffles_sv_call_renders_expected_command() -> None:
    cmd = sniffles_sv_call(
        input_bam="/tmp/sample.bam",
        reference_fasta="/tmp/ref.fa",
        output_vcf="/tmp/sv/sniffles.vcf",
        threads=8,
        sample_id="sample_a",
        min_support=5,
        min_sv_length=75,
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/sv;" in cmd
    assert "samtools index /tmp/sample.bam" in cmd
    assert "sniffles --input /tmp/sample.bam --vcf /tmp/sv/sniffles.vcf --reference /tmp/ref.fa --threads 8" in cmd
    assert "--sample-id sample_a" in cmd
    assert "--minsupport 5" in cmd
    assert "--minsvlen 75" in cmd


def test_sniffles_sv_call_prefers_launcher_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.sniffles_sv_call.tool_launcher_command",
        lambda name: "/opt/tool-envs/sniffles/bin/sniffles" if name == "sniffles" else None,
    )

    cmd = sniffles_sv_call(
        input_bam="/tmp/sample.bam",
        reference_fasta="/tmp/ref.fa",
        output_vcf="/tmp/sv/sniffles.vcf",
    )

    assert "/opt/tool-envs/sniffles/bin/sniffles --input /tmp/sample.bam" in cmd


def test_majiq_run_sets_defaults_when_missing() -> None:
    cmd = majiq_run(
        config_file="/tmp/config.ini",
        group1_bams="/tmp/control_a.bam,/tmp/control_b.bam",
        group2_bams="/tmp/treat_a.bam,/tmp/treat_b.bam",
        output_dir="/tmp/majiq_out",
    )

    assert "majiq build -j 2" in cmd
    assert "majiq deltapsi -j 2" in cmd
    assert "-n control_vs_treatment" in cmd


def test_seurat_rscript_workflow_renders_expected_command() -> None:
    cmd = seurat_rscript_workflow(
        input_matrix="/tmp/matrix.tsv",
        metadata_table="/tmp/metadata.tsv",
        output_dir="/tmp/seurat_out",
    )

    assert "seurat_workflow.R" in cmd
    assert "--matrix /tmp/matrix.tsv" in cmd
    assert "--metadata /tmp/metadata.tsv" in cmd
    assert "--output-dir /tmp/seurat_out" in cmd


def test_trinity_assemble_renders_expected_command() -> None:
    cmd = trinity_assemble(
        reads_1="/tmp/reads_R1.fastq.gz",
        reads_2="/tmp/reads_R2.fastq.gz",
        threads=16,
        max_memory_gb=64,
        output_dir="/tmp/trinity_out",
    )

    assert "set -euo pipefail;" in cmd
    assert "--seqType fq" in cmd
    assert "--left /tmp/reads_R1.fastq.gz" in cmd
    assert "--right /tmp/reads_R2.fastq.gz" in cmd
    assert "--CPU 16" in cmd
    assert "--max_memory 64G" in cmd


def test_trinity_assemble_supports_no_normalize_reads_and_launchers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.trinity_assemble.tool_launcher_command",
        lambda name: "/opt/tool-envs/trinity/bin/trinity" if name == "trinity" else None,
    )
    cmd = trinity_assemble(
        reads_1="/tmp/reads_R1.fastq.gz",
        reads_2="/tmp/reads_R2.fastq.gz",
        threads=1,
        max_memory_gb=2,
        output_dir="/tmp/trinity_out",
        no_normalize_reads=True,
    )

    assert "/opt/tool-envs/trinity/bin/trinity --seqType fq" in cmd
    assert "--no_normalize_reads" in cmd


def test_vep_annotate_renders_expected_command() -> None:
    cmd = vep_annotate(
        assembly="GRCh38",
        input_vcf="/tmp/input.vcf.gz",
        output_vcf="/tmp/output.vcf.gz",
    )

    assert "--cache --offline" in cmd
    assert "--assembly GRCh38" in cmd
    assert "-i /tmp/input.vcf.gz" in cmd
    assert "-o /tmp/output.vcf.gz" in cmd
    assert "--vcf" in cmd


@pytest.mark.parametrize(
    ("wrapper", "kwargs", "missing_arg"),
    [
        (flye_assemble, {"threads": 8, "output_dir": "/tmp/flye", "genome_size": "4.8m"}, "reads_fastq"),
        (
            mafft_align,
            {"input_fasta": "/tmp/input.fa"},
            "output_fasta",
        ),
        (
            sniffles_sv_call,
            {"input_bam": "/tmp/sample.bam", "reference_fasta": "/tmp/ref.fa"},
            "output_vcf",
        ),
        (
            limma_voom_run,
            {
                "script_path": "/tmp/limma_voom.R",
                "counts_matrix": "/tmp/counts.tsv",
                "metadata_table": "/tmp/meta.tsv",
                "design_formula": "~ condition",
                "output_dir": "/tmp/out",
            },
            "contrast",
        ),
        (
            vep_annotate,
            {"assembly": "GRCh38", "input_vcf": "/tmp/input.vcf.gz"},
            "output_vcf",
        ),
    ],
)
def test_template_wrappers_raise_for_missing_required_fields(wrapper, kwargs, missing_arg: str) -> None:
    with pytest.raises(ValueError, match=missing_arg):
        wrapper(**kwargs)
