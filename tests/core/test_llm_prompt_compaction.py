from __future__ import annotations

from bio_harness.core.llm import BioLLM


def test_compact_planner_prompt_style_uses_slim_skill_rows(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROMPT_STYLE", "compact")
    llm = BioLLM(model_name="qwen3-coder-next")
    skills = [
        {
            "name": "bcftools_call",
            "description": "Call variants from an aligned BAM using bcftools mpileup and call.",
            "parameters": {
                "reference_fasta": {"type": "path", "description": "Reference FASTA", "required": True},
                "input_bam": {"type": "path", "description": "Input BAM", "required": True},
                "output_vcf_gz": {"type": "path", "description": "Output VCF", "required": False},
            },
        }
    ]

    text = llm._format_skills_for_prompt(skills)
    assert "Parameters:" not in text
    assert "required_args=[" in text
    assert "optional_args=[" in text
    assert "bcftools_call" in text


def test_compact_planner_prompt_style_surfaces_caps_and_outputs_for_compact_models(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROMPT_STYLE", "compact")
    llm = BioLLM(model_name="gemma4:26b")
    skills = [
        {
            "name": "stringtie_quant",
            "description": "Assemble and quantify transcripts from aligned RNA-seq BAM files.",
            "capabilities": ["quantification", "reference_inputs"],
            "analysis_categories": ["transcript_quantification"],
            "canonical_output_filenames": {
                "output_gtf": "assembled.gtf",
                "gene_abundance_tsv": "gene_abundances.tsv",
            },
            "parameters": {
                "input_bam": {"type": "path", "description": "Aligned BAM", "required": True},
                "annotation_gtf": {"type": "path", "description": "Annotation GTF", "required": True},
                "threads": {"type": "integer", "description": "Thread count", "required": False},
            },
        }
    ]

    text = llm._format_skills_for_prompt(skills)
    assert "caps=[quantification, reference_inputs]" in text
    assert "categories=[transcript_quantification]" in text
    assert "outputs=[assembled.gtf, gene_abundances.tsv]" in text


def test_compact_planner_prompt_style_keeps_larger_models_lean(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROMPT_STYLE", "compact")
    llm = BioLLM(model_name="llama3.1:70b")
    skills = [
        {
            "name": "stringtie_quant",
            "description": "Assemble and quantify transcripts from aligned RNA-seq BAM files.",
            "capabilities": ["quantification"],
            "analysis_categories": ["transcript_quantification"],
            "canonical_output_filenames": {"output_gtf": "assembled.gtf"},
            "parameters": {
                "input_bam": {"type": "path", "description": "Aligned BAM", "required": True},
            },
        }
    ]

    text = llm._format_skills_for_prompt(skills)
    assert "caps=[" not in text
    assert "outputs=[" not in text


def test_full_planner_prompt_style_keeps_verbose_skill_schema(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROMPT_STYLE", "full")
    llm = BioLLM(model_name="qwen3-coder-next")
    skills = [
        {
            "name": "bash_run",
            "description": "Execute shell commands.",
            "parameters": {
                "command": {"type": "string", "description": "Command to run", "required": True},
            },
        }
    ]

    text = llm._format_skills_for_prompt(skills)
    assert "Parameters:" in text
    assert "command (string)" in text


def test_full_planner_prompt_style_includes_output_hints_for_compact_models(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROMPT_STYLE", "full")
    llm = BioLLM(model_name="gemma4:26b")
    skills = [
        {
            "name": "stringtie_quant",
            "description": "Transcript assembly and quantification.",
            "capabilities": ["quantification"],
            "analysis_categories": ["transcript_quantification"],
            "canonical_output_filenames": {"output_gtf": "assembled.gtf"},
            "parameters": {
                "input_bam": {"type": "path", "description": "Aligned BAM", "required": True},
            },
        }
    ]

    text = llm._format_skills_for_prompt(skills)
    assert "Canonical outputs: assembled.gtf" in text
    assert "Capabilities: quantification" in text
