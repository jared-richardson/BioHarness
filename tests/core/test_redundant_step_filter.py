"""Tests for redundant step filtering in _patch_llm_plan_with_template.

Verifies that inline R/Python scripts are stripped when a dedicated skill step
exists, but that external script invocations are preserved.
"""
from __future__ import annotations

from bio_harness.core.protocol_grounding import _patch_llm_plan_with_template


def _make_plan(steps):
    return {"plan": [{**s, "step_id": i} for i, s in enumerate(steps, 1)]}


def _tool_names(plan):
    return [s.get("tool_name", "") for s in plan.get("plan", [])]


# ── Inline Rscript -e is stripped when deseq2_run is in template ────────

def test_inline_rscript_e_stripped_with_deseq2_skill():
    """Rscript -e 'library(DESeq2)' should be stripped when template has deseq2_run."""
    llm_plan = _make_plan([
        {"tool_name": "star_align", "arguments": {}},
        {"tool_name": "featurecounts_run", "arguments": {}},
        {"tool_name": "bash_run", "arguments": {
            "command": 'Rscript -e \'library(DESeq2); dds <- DESeqDataSetFromMatrix()\''
        }},
        {"tool_name": "deseq2_run", "arguments": {}},
    ])
    template_plan = _make_plan([
        {"tool_name": "star_align", "arguments": {}},
        {"tool_name": "featurecounts_run", "arguments": {}},
        {"tool_name": "deseq2_run", "arguments": {}},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    assert "bash_run" not in tools, f"Inline Rscript -e should have been stripped, got: {tools}"
    assert meta["extra_llm_steps_filtered"] >= 1


def test_heredoc_r_stripped_with_edger_skill():
    """Rscript with heredoc (<<) containing library() should be stripped."""
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": 'Rscript --no-save <<EOF\nlibrary(edgeR)\nd <- DGEList(counts)\nEOF'
        }},
        {"tool_name": "edger_run", "arguments": {}},
    ])
    template_plan = _make_plan([
        {"tool_name": "edger_run", "arguments": {}},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    assert tools.count("bash_run") == 0
    assert meta["extra_llm_steps_filtered"] >= 1


# ── External Rscript invocations are PRESERVED ──────────────────────────

def test_external_rscript_preserved_with_deseq2_skill():
    """Rscript some_script.R (external) should NOT be stripped even when
    deseq2_run is in the template — it may be legitimate preprocessing."""
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": "Rscript format_counts.R --input raw.txt --output counts.txt"
        }},
        {"tool_name": "deseq2_run", "arguments": {}},
    ])
    template_plan = _make_plan([
        {"tool_name": "deseq2_run", "arguments": {}},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    assert "bash_run" in tools, f"External Rscript call should be preserved, got: {tools}"
    assert meta["extra_llm_steps_filtered"] == 0


def test_bash_run_without_r_or_python_preserved():
    """Generic bash_run steps (e.g., mkdir, samtools) should never be stripped."""
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": "samtools sort -o sorted.bam input.bam && samtools index sorted.bam"
        }},
        {"tool_name": "deseq2_run", "arguments": {}},
    ])
    template_plan = _make_plan([
        {"tool_name": "deseq2_run", "arguments": {}},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    assert "bash_run" in tools


# ── Keyword-based filtering still works ─────────────────────────────────

def test_keyword_filter_strips_salmon_bash_when_skill_present():
    """bash_run with 'salmon quant' should be stripped when salmon_quant is in template."""
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": "salmon quant -i idx -l A -1 r1.fq -2 r2.fq -o quant"
        }},
        {"tool_name": "salmon_quant", "arguments": {}},
    ])
    template_plan = _make_plan([
        {"tool_name": "salmon_quant", "arguments": {}},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    assert tools.count("bash_run") == 0
    assert meta["extra_llm_steps_filtered"] >= 1


# ── Inline Python filtering ────────────────────────────────────────────

def test_inline_python_stripped_with_scanpy_skill():
    """python3 -c 'import scanpy' should be stripped when scanpy_workflow is in template."""
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": 'python3 -c "import scanpy as sc; adata = sc.read_h5ad(\'data.h5ad\')"'
        }},
        {"tool_name": "scanpy_workflow", "arguments": {}},
    ])
    template_plan = _make_plan([
        {"tool_name": "scanpy_workflow", "arguments": {}},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    assert tools.count("bash_run") == 0


def test_external_python_script_preserved_with_scanpy():
    """python3 preprocess.py should NOT be stripped, even with scanpy_workflow."""
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": "python3 preprocess.py --input data.csv --output cleaned.csv"
        }},
        {"tool_name": "scanpy_workflow", "arguments": {}},
    ])
    template_plan = _make_plan([
        {"tool_name": "scanpy_workflow", "arguments": {}},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    assert "bash_run" in tools


# ── Step ordering: extra LLM steps AFTER template steps ──────────────

def test_extra_llm_steps_placed_after_template_steps():
    """Extra LLM steps should appear AFTER all template steps.

    Regression test: transcript-quant failed because the LLM's awk step
    (writing the deliverable from quant.sf) was placed before the template's
    salmon_quant step, producing an empty output file.
    """
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": "mkdir -p /out/final"
        }},
        {"tool_name": "bash_run", "arguments": {
            "command": "awk 'NR>1 {print $1\"\\t\"$5}' /out/quant/quant.sf > /out/final/counts.tsv"
        }},
        {"tool_name": "salmon_quant", "arguments": {
            "transcriptome_fasta": "/data/transcriptome.fa",
        }},
    ])
    template_plan = _make_plan([
        {"tool_name": "salmon_quant", "arguments": {
            "transcriptome_fasta": "/data/transcriptome.fa",
            "output_dir": "/out/quant",
        }},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    # salmon_quant (template) must come before extra bash_run steps
    salmon_idx = tools.index("salmon_quant")
    bash_indices = [i for i, t in enumerate(tools) if t == "bash_run"]
    assert all(
        bi > salmon_idx for bi in bash_indices
    ), f"Extra bash steps should come after salmon_quant, got order: {tools}"


def test_extra_llm_steps_after_multi_step_template():
    """With a 2-step template, extra LLM steps still go at the end."""
    llm_plan = _make_plan([
        {"tool_name": "bash_run", "arguments": {
            "command": "snpeff databases -build ref.fa genes.gff"
        }},
        {"tool_name": "snpeff_annotate", "arguments": {}},
        {"tool_name": "bash_run", "arguments": {
            "command": "grep 'IMPACT=HIGH' ann.vcf > /out/filtered.vcf"
        }},
    ])
    template_plan = _make_plan([
        {"tool_name": "snpeff_annotate", "arguments": {
            "output_vcf": "/out/annotated.vcf",
        }},
        {"tool_name": "bash_run", "arguments": {
            "command": 'SnpSift filter "(ANN[*].IMPACT = \'HIGH\')" /out/annotated.vcf > /out/filtered.vcf'
        }},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    # snpeff_annotate should come first, then the template's SnpSift bash_run,
    # then any extra LLM steps (if not filtered).
    annot_idx = tools.index("snpeff_annotate")
    # snpeff_annotate should be first
    assert annot_idx == 0, f"snpeff_annotate should be first, got: {tools}"
    # The LLM's grep step writes to the same output as the template's SnpSift
    # step, so it should be filtered out.  At least 2 steps remain.
    assert len(tools) >= 2


# ── Output-path redundancy filter ────────────────────────────────────

def test_extra_bash_writing_same_output_as_template_is_filtered():
    """LLM bash step redirecting to same output as template step is redundant.

    Regression test: variant-annotation failed because the LLM's grep step
    wrote an empty file to filtered_pathogenic.vcf, overwriting the correct
    SnpSift filter output.
    """
    llm_plan = _make_plan([
        {"tool_name": "snpeff_annotate", "arguments": {}},
        {"tool_name": "bash_run", "arguments": {
            "command": "grep -E 'IMPACT=(HIGH|MODERATE)' /out/annotated.vcf > /out/filtered.vcf"
        }},
    ])
    template_plan = _make_plan([
        {"tool_name": "snpeff_annotate", "arguments": {}},
        {"tool_name": "bash_run", "arguments": {
            "command": 'SnpSift filter "(ANN[*].IMPACT = \'HIGH\')" /out/annotated.vcf > /out/filtered.vcf'
        }},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    # The LLM's grep step writes to /out/filtered.vcf which is the same as
    # the template's SnpSift step output. It should be filtered.
    assert meta["extra_llm_steps_filtered"] >= 1
    # Only 2 steps should remain: snpeff_annotate + SnpSift filter
    assert len(tools) == 2, f"Expected 2 steps, got {len(tools)}: {tools}"


def test_extra_bash_writing_different_output_is_preserved():
    """LLM bash step with a different output path should NOT be filtered."""
    llm_plan = _make_plan([
        {"tool_name": "salmon_quant", "arguments": {}},
        {"tool_name": "bash_run", "arguments": {
            "command": "awk 'NR>1' /out/quant.sf > /out/final/summary.tsv"
        }},
    ])
    template_plan = _make_plan([
        {"tool_name": "salmon_quant", "arguments": {
            "output_dir": "/out/quant",
        }},
    ])

    patched, meta = _patch_llm_plan_with_template(llm_plan, template_plan)
    tools = _tool_names(patched)
    # The awk step writes to /out/final/summary.tsv which is NOT a template output
    assert "bash_run" in tools
    assert meta["extra_llm_steps_filtered"] == 0
