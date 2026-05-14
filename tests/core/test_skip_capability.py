"""Tests for _steps_cover_same_capability and the validation-skip safety guard."""
from __future__ import annotations

from bio_harness.agents.orchestrator import Orchestrator


def _covers(failed: str, next_t: str, cmd: str = "") -> bool:
    return Orchestrator._steps_cover_same_capability(failed, next_t, cmd)


# ── Same tool duplicates ────────────────────────────────────────────────

def test_same_tool_always_covers():
    assert _covers("deseq2_run", "deseq2_run") is True


# ── Inline R script → R skill ───────────────────────────────────────────

def test_inline_r_script_covers_deseq2():
    cmd = 'Rscript -e \'library(DESeq2); dds <- DESeqDataSetFromMatrix(...)\''
    assert _covers("bash_run", "deseq2_run", cmd) is True


def test_heredoc_r_script_covers_edger():
    cmd = 'Rscript --no-save <<EOF\nlibrary(edgeR); d <- DGEList(...)\nEOF'
    assert _covers("bash_run", "edger_run", cmd) is True


def test_inline_r_with_library_semicolons():
    cmd = 'Rscript -e "library(DESeq2); dds <- DESeqDataSetFromMatrix(countData=counts, colData=coldata, design=~condition); dds <- DESeq(dds)"'
    assert _covers("bash_run", "deseq2_run", cmd) is True


def test_external_r_script_does_NOT_cover_deseq2():
    """An external Rscript invocation (e.g., preprocessing) should NOT be
    considered the same capability as a dedicated DE skill step."""
    cmd = "Rscript trim_adapters.R --input reads.fastq --output trimmed.fastq"
    assert _covers("bash_run", "deseq2_run", cmd) is False


def test_external_r_format_script_does_NOT_cover():
    cmd = "Rscript format_counts_matrix.R --input raw.csv --output counts.txt"
    assert _covers("bash_run", "edger_run", cmd) is False


# ── Inline Python → Python skill ────────────────────────────────────────

def test_inline_python_covers_scanpy():
    cmd = 'python3 -c "import scanpy as sc; adata = sc.read_h5ad(\'data.h5ad\')"'
    assert _covers("bash_run", "scanpy_workflow", cmd) is True


def test_external_python_script_does_NOT_cover():
    cmd = "python3 preprocess.py --input data.csv"
    assert _covers("bash_run", "scanpy_workflow", cmd) is False


# ── bash_run with specific bioinformatics tool → skill ──────────────────

def test_bash_star_covers_star_align():
    cmd = "STAR --runThreadN 8 --genomeDir /ref/star_idx --readFilesIn r1.fq r2.fq"
    assert _covers("bash_run", "star_align", cmd) is True


def test_bash_salmon_covers_salmon_quant():
    cmd = "salmon quant -i /ref/salmon_idx -l A -1 r1.fq -2 r2.fq -o quant"
    assert _covers("bash_run", "salmon_quant", cmd) is True


def test_bash_gatk_covers_gatk_hc():
    cmd = "gatk HaplotypeCaller -R ref.fa -I sample.bam -O output.vcf"
    assert _covers("bash_run", "gatk_haplotypecaller", cmd) is True


# ── Unrelated steps do NOT cover ────────────────────────────────────────

def test_unrelated_tools_do_not_cover():
    assert _covers("bash_run", "deseq2_run", "samtools sort -o sorted.bam input.bam") is False


def test_different_skill_tools_do_not_cover():
    assert _covers("deseq2_run", "star_align") is False


def test_bash_without_matching_content():
    cmd = "echo hello world"
    assert _covers("bash_run", "salmon_quant", cmd) is False
